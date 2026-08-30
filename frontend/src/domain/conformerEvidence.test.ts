import { describe, expect, it } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import {
    calculationTypeCounts,
    conformerLabel,
    geometryConvergence,
    partitionByConformerLink,
    statmechConformerGroupRef,
    thermoConformerGroupRef,
} from "./conformerEvidence"

// THREE distinct conformer basins -- the three-way partition
// (this conformer / a DIFFERENT named conformer / no link at all) cannot be
// proven correct against fewer than three: two conformers cannot show that
// a record linked to "the other" one is labeled with THAT conformer's own
// name, rather than lumped into a single undifferentiated "other" bucket.
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
        observations: [],
        calculations: [],
        geometries: [],
        ...overrides,
    } as ConformerProjection
}

const conformerOne = group()
const conformerTwo = group({ conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2" } })
const conformerThree = group({ conformer_group: { conformer_group_ref: "cg_three", label: "conformer_3" } })
const allConformers = [conformerOne, conformerTwo, conformerThree]

describe("conformerLabel", () => {
    it("renders a deposited label matching the archive's auto-numbering convention as 'Conformer Group N'", () => {
        expect(conformerLabel(conformerOne)).toBe("Conformer Group 1")
        expect(conformerLabel(conformerTwo)).toBe("Conformer Group 2")
        // Not just single digits -- the numeral is read verbatim, not
        // truncated or re-derived from position.
        const many = group({ conformer_group: { conformer_group_ref: "cg_many", label: "conformer_42" } })
        expect(conformerLabel(many)).toBe("Conformer Group 42")
    })

    it("renders a non-matching deposited label verbatim, never coerced into the 'Conformer Group N' shape", () => {
        const depositorNamed = group({ conformer_group: { conformer_group_ref: "cg_x", label: "anti-periplanar" } })
        expect(conformerLabel(depositorNamed)).toBe("anti-periplanar")
        // Contains the pattern's own word but doesn't MATCH it (extra
        // suffix) -- must not be mistaken for the auto-numbered case.
        const suffixed = group({ conformer_group: { conformer_group_ref: "cg_y", label: "conformer_1_reoptimized" } })
        expect(conformerLabel(suffixed)).toBe("conformer_1_reoptimized")
        // Leading zero / non-numeric suffix: still not a match.
        const nonNumeric = group({ conformer_group: { conformer_group_ref: "cg_z", label: "conformer_a" } })
        expect(conformerLabel(nonNumeric)).toBe("conformer_a")
    })

    it("falls back to the group's own stable ref, verbatim, when no label was deposited -- never inventing a display name", () => {
        const unlabeled = group({ conformer_group: { conformer_group_ref: "cg_unlabeled", label: null } })
        expect(conformerLabel(unlabeled)).toBe("cg_unlabeled")
    })
})

describe("calculationTypeCounts", () => {
    it("counts calculation rows by stage, ordered opt/freq/sp, distinct from coverage", () => {
        const conformer = group({
            calculations: [
                { calculation_ref: "c1", type: "sp" },
                { calculation_ref: "c2", type: "opt" },
                { calculation_ref: "c3", type: "opt" },
                { calculation_ref: "c4", type: "freq" },
                { calculation_ref: "c5", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        expect(calculationTypeCounts(conformer)).toEqual([
            { type: "opt", count: 3 },
            { type: "freq", count: 1 },
            { type: "sp", count: 1 },
        ])
    })

    it("appends an unrecognized stage after the three known ones, never dropping it", () => {
        const conformer = group({
            calculations: [
                { calculation_ref: "c1", type: "sp" },
                { calculation_ref: "c2", type: "irc" },
            ] as ConformerProjection["calculations"],
        })
        expect(calculationTypeCounts(conformer)).toEqual([
            { type: "sp", count: 1 },
            { type: "irc", count: 1 },
        ])
    })

    it("returns an empty list, not an error, when no calculation rows are projected", () => {
        expect(calculationTypeCounts(group({ calculations: [] }))).toEqual([])
        expect(calculationTypeCounts(group({ calculations: null } as Partial<ConformerProjection>))).toEqual([])
    })
})

describe("geometryConvergence", () => {
    it("counts how many calculation outputs converge on each distinct geometry", () => {
        const conformer = group({
            geometries: [
                { calculation_ref: "c1", geometry: { geometry_ref: "geom_a" } },
                { calculation_ref: "c2", geometry: { geometry_ref: "geom_a" } },
                { calculation_ref: "c3", geometry: { geometry_ref: "geom_a" } },
                { calculation_ref: "c4", geometry: { geometry_ref: "geom_a" } },
                { calculation_ref: "c5", geometry: { geometry_ref: "geom_b" } },
                { calculation_ref: "c6", geometry: { geometry_ref: "geom_b" } },
                { calculation_ref: "c7", geometry: { geometry_ref: "geom_b" } },
            ] as ConformerProjection["geometries"],
        })
        expect(geometryConvergence(conformer)).toEqual([
            { geometryRef: "geom_a", calculationCount: 4 },
            { geometryRef: "geom_b", calculationCount: 3 },
        ])
    })

    it("returns an empty list, not an error, when no geometry links are projected", () => {
        expect(geometryConvergence(group({ geometries: [] }))).toEqual([])
        expect(geometryConvergence(group({ geometries: null } as Partial<ConformerProjection>))).toEqual([])
    })
})

describe("thermoConformerGroupRef", () => {
    it("reads the real conformer_group_ref off provenance (PR #285)", () => {
        expect(thermoConformerGroupRef({ provenance: { conformer_group_ref: "cg_one" } })).toBe("cg_one")
    })

    it("returns null for population B (no resolvable primary calculation), never a guess", () => {
        expect(thermoConformerGroupRef({ provenance: { conformer_group_ref: null } })).toBeNull()
        expect(thermoConformerGroupRef({ provenance: null })).toBeNull()
        expect(thermoConformerGroupRef({})).toBeNull()
    })
})

describe("statmechConformerGroupRef", () => {
    it("reads the real conformer_group_ref off the include=conformers field", () => {
        expect(statmechConformerGroupRef([{ conformer_group_ref: "cg_two" }])).toBe("cg_two")
    })

    it("returns null for an unrequested/empty conformer context, not an error", () => {
        expect(statmechConformerGroupRef(null)).toBeNull()
        expect(statmechConformerGroupRef(undefined)).toBeNull()
        expect(statmechConformerGroupRef([])).toBeNull()
    })
})

describe("partitionByConformerLink", () => {
    type Row = { id: string; ref: string | null }
    const linkedRef = (row: Row) => row.ref

    it("splits into thisConformer / otherConformers (one bucket per DISTINCT other conformer, named) / noLink", () => {
        const rows: Row[] = [
            { id: "a", ref: "cg_one" }, // this conformer (selected)
            { id: "b", ref: "cg_two" }, // a different, named conformer
            { id: "c", ref: "cg_three" }, // yet another different, named conformer
            { id: "d", ref: "cg_two" }, // same "other" conformer as b -- must land in the SAME bucket, not a new one
            { id: "e", ref: null }, // no link at all
        ]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRef)
        expect(result.thisConformer).toEqual([{ id: "a", ref: "cg_one" }])
        expect(result.noLink).toEqual([{ id: "e", ref: null }])
        // Two distinct other conformers, each its own labeled bucket --
        // never merged into one generic "other" group, and "b"/"d" (both
        // cg_two) land together in the SAME bucket.
        expect(result.otherConformers).toHaveLength(2)
        const byRef = new Map(result.otherConformers.map((bucket) => [bucket.ref, bucket]))
        expect(byRef.get("cg_two")).toEqual({ ref: "cg_two", label: "Conformer Group 2", records: [{ id: "b", ref: "cg_two" }, { id: "d", ref: "cg_two" }] })
        expect(byRef.get("cg_three")).toEqual({ ref: "cg_three", label: "Conformer Group 3", records: [{ id: "c", ref: "cg_three" }] })
    })

    it("never claims 'no link' about a record the wire attributes to a different conformer -- the exact regression this replaced", () => {
        const rows: Row[] = [{ id: "a", ref: "cg_two" }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRef)
        expect(result.noLink).toEqual([])
        expect(result.thisConformer).toEqual([])
        expect(result.otherConformers).toEqual([{ ref: "cg_two", label: "Conformer Group 2", records: [{ id: "a", ref: "cg_two" }] }])
    })

    it("falls back to the raw ref as the label when the linked group isn't in the loaded conformer list", () => {
        const rows: Row[] = [{ id: "a", ref: "cg_unknown" }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRef)
        expect(result.otherConformers).toEqual([{ ref: "cg_unknown", label: "cg_unknown", records: [{ id: "a", ref: "cg_unknown" }] }])
    })

    it("puts everything in noLink when nothing links anywhere, and nothing in the other two groups", () => {
        const rows: Row[] = [{ id: "a", ref: null }, { id: "b", ref: null }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRef)
        expect(result.thisConformer).toEqual([])
        expect(result.otherConformers).toEqual([])
        expect(result.noLink).toEqual(rows)
    })
})
