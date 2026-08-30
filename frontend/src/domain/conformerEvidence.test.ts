import { describe, expect, it } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import {
    calculationTypeCounts,
    conformerDisplayNumber,
    conformerLabel,
    describeConformerEvidence,
    geometryConvergence,
    optimizationStaging,
    partitionByConformerLink,
    sortConformersForDisplay,
    statmechConformerGroupRefs,
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

    it("treats a blank or whitespace-only deposited label the same as no label -- falls back to the ref, never an empty display name", () => {
        const empty = group({ conformer_group: { conformer_group_ref: "cg_empty", label: "" } })
        expect(conformerLabel(empty)).toBe("cg_empty")
        const whitespace = group({ conformer_group: { conformer_group_ref: "cg_ws", label: "   " } })
        expect(conformerLabel(whitespace)).toBe("cg_ws")
    })

    it("preserves a leading zero in an auto-numbered label -- honest under the stated rule, never re-derived", () => {
        const leadingZero = group({ conformer_group: { conformer_group_ref: "cg_lz", label: "conformer_01" } })
        expect(conformerLabel(leadingZero)).toBe("Conformer Group 01")
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

describe("statmechConformerGroupRefs", () => {
    it("reads every conformer_group_ref off the include=conformers field, in the wire's own order", () => {
        expect(statmechConformerGroupRefs([{ conformer_group_ref: "cg_two" }])).toEqual(["cg_two"])
        // The real, on-the-wire case that exposed the bug: an ensemble-level
        // statmech treatment naming THREE groups. An earlier version of this
        // function read only `[0]`, so this multi-element case is the one
        // that must never regress back to a single ref.
        expect(statmechConformerGroupRefs([
            { conformer_group_ref: "cg_one" },
            { conformer_group_ref: "cg_two" },
            { conformer_group_ref: "cg_three" },
        ])).toEqual(["cg_one", "cg_two", "cg_three"])
    })

    it("returns an empty array for an unrequested/empty conformer context, not an error", () => {
        expect(statmechConformerGroupRefs(null)).toEqual([])
        expect(statmechConformerGroupRefs(undefined)).toEqual([])
        expect(statmechConformerGroupRefs([])).toEqual([])
    })
})

describe("partitionByConformerLink", () => {
    type Row = { id: string; refs: string[] }
    const linkedRefs = (row: Row) => row.refs

    it("splits into thisConformer / otherConformers (one bucket per DISTINCT other conformer, named) / noLink", () => {
        const rows: Row[] = [
            { id: "a", refs: ["cg_one"] }, // this conformer (selected)
            { id: "b", refs: ["cg_two"] }, // a different, named conformer
            { id: "c", refs: ["cg_three"] }, // yet another different, named conformer
            { id: "d", refs: ["cg_two"] }, // same "other" conformer as b -- must land in the SAME bucket, not a new one
            { id: "e", refs: [] }, // no link at all
        ]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRefs)
        expect(result.thisConformer).toEqual([{ id: "a", refs: ["cg_one"] }])
        expect(result.noLink).toEqual([{ id: "e", refs: [] }])
        // Two distinct other conformers, each its own labeled bucket --
        // never merged into one generic "other" group, and "b"/"d" (both
        // cg_two) land together in the SAME bucket.
        expect(result.otherConformers).toHaveLength(2)
        const byRef = new Map(result.otherConformers.map((bucket) => [bucket.ref, bucket]))
        expect(byRef.get("cg_two")).toEqual({ ref: "cg_two", label: "Conformer Group 2", records: [{ id: "b", refs: ["cg_two"] }, { id: "d", refs: ["cg_two"] }] })
        expect(byRef.get("cg_three")).toEqual({ ref: "cg_three", label: "Conformer Group 3", records: [{ id: "c", refs: ["cg_three"] }] })
    })

    it("never claims 'no link' about a record the wire attributes to a different conformer -- the exact regression this replaced", () => {
        const rows: Row[] = [{ id: "a", refs: ["cg_two"] }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRefs)
        expect(result.noLink).toEqual([])
        expect(result.thisConformer).toEqual([])
        expect(result.otherConformers).toEqual([{ ref: "cg_two", label: "Conformer Group 2", records: [{ id: "a", refs: ["cg_two"] }] }])
    })

    it("falls back to the raw ref as the label when the linked group isn't in the loaded conformer list", () => {
        const rows: Row[] = [{ id: "a", refs: ["cg_unknown"] }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRefs)
        expect(result.otherConformers).toEqual([{ ref: "cg_unknown", label: "cg_unknown", records: [{ id: "a", refs: ["cg_unknown"] }] }])
    })

    it("puts everything in noLink when nothing links anywhere, and nothing in the other two groups", () => {
        const rows: Row[] = [{ id: "a", refs: [] }, { id: "b", refs: [] }]
        const result = partitionByConformerLink(rows, allConformers, "cg_one", linkedRefs)
        expect(result.thisConformer).toEqual([])
        expect(result.otherConformers).toEqual([])
        expect(result.noLink).toEqual(rows)
    })

    // The bug this whole section exists to close: "Thermochemistry is
    // bust... it always shows From Conformer Group 1 even if I click
    // another group." Measured live on spe_mbdqifmaclaakukr7agxbuq3wa: one
    // statmech record names all three of that entry's conformer groups
    // (`['conformer_1', 'conformer_2', 'conformer_3']`). A record naming
    // the SELECTED group anywhere in its list must file under
    // `thisConformer`, regardless of which OTHER groups it also names and
    // regardless of which position the selected ref sits at in the list --
    // a first-match implementation (reading only `refs[0]`) passes when the
    // selected group happens to be first and fails for second/third, which
    // is exactly the production bug.
    it("files a record naming multiple groups under the SELECTED one, whichever position it's named in -- never a first-match", () => {
        const multiGroupRecord: Row = { id: "ensemble", refs: ["cg_one", "cg_two", "cg_three"] }

        const selectedFirst = partitionByConformerLink([multiGroupRecord], allConformers, "cg_one", linkedRefs)
        expect(selectedFirst.thisConformer).toEqual([multiGroupRecord])
        expect(selectedFirst.otherConformers).toEqual([])

        const selectedSecond = partitionByConformerLink([multiGroupRecord], allConformers, "cg_two", linkedRefs)
        expect(selectedSecond.thisConformer).toEqual([multiGroupRecord])
        expect(selectedSecond.otherConformers).toEqual([])

        const selectedThird = partitionByConformerLink([multiGroupRecord], allConformers, "cg_three", linkedRefs)
        expect(selectedThird.thisConformer).toEqual([multiGroupRecord])
        expect(selectedThird.otherConformers).toEqual([])
    })

    it("files a record naming several groups, NONE of them selected, under every one of those groups' own buckets", () => {
        const spanning: Row = { id: "ensemble", refs: ["cg_two", "cg_three"] }
        const result = partitionByConformerLink([spanning], allConformers, "cg_one", linkedRefs)
        expect(result.thisConformer).toEqual([])
        expect(result.otherConformers).toHaveLength(2)
        const byRef = new Map(result.otherConformers.map((bucket) => [bucket.ref, bucket]))
        expect(byRef.get("cg_two")?.records).toEqual([spanning])
        expect(byRef.get("cg_three")?.records).toEqual([spanning])
    })
})

describe("conformerDisplayNumber", () => {
    it("parses the numeral out of an auto-numbered label as a NUMBER", () => {
        expect(conformerDisplayNumber(group({ conformer_group: { conformer_group_ref: "cg_a", label: "conformer_7" } }))).toBe(7)
        expect(conformerDisplayNumber(group({ conformer_group: { conformer_group_ref: "cg_b", label: "conformer_10" } }))).toBe(10)
    })

    it("returns null for anything the auto-numbering pattern doesn't match", () => {
        expect(conformerDisplayNumber(group({ conformer_group: { conformer_group_ref: "cg_c", label: null } }))).toBeNull()
        expect(conformerDisplayNumber(group({ conformer_group: { conformer_group_ref: "cg_d", label: "anti-periplanar" } }))).toBeNull()
        expect(conformerDisplayNumber(group({ conformer_group: { conformer_group_ref: "cg_e", label: "conformer_1_reoptimized" } }))).toBeNull()
    })
})

describe("sortConformersForDisplay", () => {
    it("sorts numbered conformers ascending by the PARSED numeral, not string order -- conformer_10 after conformer_9, never between 1 and 2", () => {
        const c1 = group({ conformer_group: { conformer_group_ref: "cg_1", label: "conformer_1" } })
        const c2 = group({ conformer_group: { conformer_group_ref: "cg_2", label: "conformer_2" } })
        const c9 = group({ conformer_group: { conformer_group_ref: "cg_9", label: "conformer_9" } })
        const c10 = group({ conformer_group: { conformer_group_ref: "cg_10", label: "conformer_10" } })
        // A lexicographic sort of the LABEL passes a 1/2/3 fixture and fails
        // only once a two-digit numeral is in the mix ("10" < "2" as
        // strings) -- this is the fixture that actually exercises that.
        const sorted = sortConformersForDisplay([c10, c2, c9, c1])
        expect(sorted.map((c) => c.conformer_group.conformer_group_ref)).toEqual(["cg_1", "cg_2", "cg_9", "cg_10"])
    })

    it("sorts non-numbered conformers after every numbered one, alphabetically by display label", () => {
        const c1 = group({ conformer_group: { conformer_group_ref: "cg_1", label: "conformer_1" } })
        const named = group({ conformer_group: { conformer_group_ref: "cg_named", label: "anti-periplanar" } })
        const unlabeled = group({ conformer_group: { conformer_group_ref: "cg_unlabeled", label: null } })
        const other = group({ conformer_group: { conformer_group_ref: "cg_other", label: "gauche" } })
        const sorted = sortConformersForDisplay([unlabeled, other, c1, named])
        // Numbered first (cg_1), then non-numbered alphabetically by display
        // label: "anti-periplanar" < "cg_unlabeled" (its own ref, since it
        // has no label) < "gauche".
        expect(sorted.map((c) => c.conformer_group.conformer_group_ref)).toEqual(["cg_1", "cg_named", "cg_unlabeled", "cg_other"])
    })

    it("never mutates the input array, and produces a total order stable across repeated calls", () => {
        const c2 = group({ conformer_group: { conformer_group_ref: "cg_2", label: "conformer_2" } })
        const c1 = group({ conformer_group: { conformer_group_ref: "cg_1", label: "conformer_1" } })
        const input = [c2, c1]
        const sorted = sortConformersForDisplay(input)
        expect(input).toEqual([c2, c1]) // untouched
        expect(sorted).not.toBe(input)
        expect(sorted.map((c) => c.conformer_group.conformer_group_ref)).toEqual(["cg_1", "cg_2"])
        // Same input, called again -- same order, never a coin-flip.
        expect(sortConformersForDisplay(input).map((c) => c.conformer_group.conformer_group_ref)).toEqual(["cg_1", "cg_2"])
    })
})

// A per-observation fixture builder: `types` is that ONE observation's own
// raw calculation-type list, exactly as `include=calculations` on
// `conformers/search` would return it nested under `observations[]`.
function observationWith(ref: string, types: string[]): NonNullable<ConformerProjection["observations"]>[number] {
    return {
        conformer_observation: { conformer_observation_ref: ref },
        calculations: types.map((type, index) => ({ calculation_ref: `${ref}_${index}`, type })),
    } as NonNullable<ConformerProjection["observations"]>[number]
}

describe("optimizationStaging", () => {
    it("is 'unknown' when the calculation breakdown hasn't loaded at all", () => {
        const conformer = group({ calculations: null } as Partial<ConformerProjection>)
        expect(optimizationStaging(conformer)).toEqual({ kind: "unknown" })
    })

    it("falls back to 'aggregate' when the chain count exceeds observations-with-opt -- an independent (non-collapsing) chain could be hiding behind one observation's raw row count", () => {
        // 2 observations, both covered by opt, but 3 CHAINS -- one
        // observation is holding two independent chains, which raw counts
        // alone cannot distinguish from one two-stage chain.
        const conformer = group({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 4, optimization_chain_count: 3, geometry_count: 1,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            observations: [observationWith("co_a", ["opt", "opt"]), observationWith("co_b", ["opt", "opt"])],
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" },
                { calculation_ref: "c3", type: "opt" }, { calculation_ref: "c4", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        const staging = optimizationStaging(conformer)
        expect(staging).toMatchObject({ kind: "aggregate", rawOptCount: 4, chainCount: 3, stagedRowCount: 1 })
    })

    // Finding 5 of the BLOCK review: the safety guard's `!==` was only
    // tested in the direction where the chain count EXCEEDS
    // observations-with-opt (the fixture above). The `<` direction is real
    // too -- the backend's `_feeds_a_refinement_on_the_same_observation`
    // does not constrain the superseding calculation's TYPE, so an opt row
    // whose `optimized_from` child is a non-opt calculation on the same
    // observation is superseded (contributes 0 chains) while still counting
    // toward `evidence_coverage.opt` (1). A reviewer's `!==` -> `>` mutation
    // passed all 347 existing tests specifically because no fixture ever
    // exercised chainCount < coverageOpt -- this one does.
    it("falls back to 'aggregate' when the chain count is LESS THAN observations-with-opt, not just greater", () => {
        const conformer = group({
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 1, optimization_chain_count: 0, geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            observations: [observationWith("co_a", ["opt"])],
            calculations: [{ calculation_ref: "c1", type: "opt" }] as ConformerProjection["calculations"],
        })
        expect(optimizationStaging(conformer).kind).toBe("aggregate")
    })

    it("falls back to 'aggregate' when not every observation's own calculation list is loaded, even if the chain count would otherwise match", () => {
        const conformer = group({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 3, optimization_chain_count: 2, geometry_count: 1,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            observations: [
                observationWith("co_a", ["opt", "opt"]),
                { conformer_observation: { conformer_observation_ref: "co_b" }, calculations: null } as NonNullable<ConformerProjection["observations"]>[number],
            ],
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" }, { calculation_ref: "c3", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        expect(optimizationStaging(conformer).kind).toBe("aggregate")
    })

    it("attributes an exact per-observation stage count when the safety condition holds (chain count == observations with opt)", () => {
        const conformer = group({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 3, optimization_chain_count: 2, geometry_count: 1,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            observations: [observationWith("co_a", ["opt", "opt"]), observationWith("co_b", ["opt"])],
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" }, { calculation_ref: "c3", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        const staging = optimizationStaging(conformer)
        expect(staging.kind).toBe("per-observation")
        if (staging.kind === "per-observation") {
            expect(staging.perObservation).toEqual(new Map([["co_a", 2], ["co_b", 1]]))
        }
    })
})

describe("describeConformerEvidence", () => {
    it("says plainly when a conformer genuinely has no observations", () => {
        expect(describeConformerEvidence(group({ observations_summary: { total: 0 } })))
            .toBe("No observations are deposited for this conformer yet.")
    })

    it("never reads the sp coverage count where the freq coverage belongs, or vice versa", () => {
        const conformer = group({
            observations_summary: { total: 4 },
            evidence_summary: {
                calculation_count: 4, optimization_chain_count: 4, geometry_count: 1,
                evidence_coverage: { opt: 4, freq: 4, sp: 1 }, levels_of_theory: {},
            },
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" },
                { calculation_ref: "c3", type: "opt" }, { calculation_ref: "c4", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        const story = describeConformerEvidence(conformer)
        expect(story).toContain("Every sighting got a frequency calculation.")
        expect(story).toContain("One of the four sightings got a single-point energy.")
        expect(story).not.toContain("Every sighting got a single-point energy.")
    })

    it("never reads the chain count where the row count belongs, or vice versa, in the aggregate sentence", () => {
        const conformer = group({
            observations_summary: { total: 4 },
            evidence_summary: {
                calculation_count: 7, optimization_chain_count: 4, geometry_count: 1,
                evidence_coverage: { opt: 4, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" }, { calculation_ref: "c3", type: "opt" },
                { calculation_ref: "c4", type: "opt" }, { calculation_ref: "c5", type: "opt" }, { calculation_ref: "c6", type: "opt" },
                { calculation_ref: "c7", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        // 7 raw opt rows, 4 chains, 3 staged -- three mutually distinct
        // numbers, so a swap of any pair against another is observable.
        const story = describeConformerEvidence(conformer)
        expect(story).toContain("Seven optimisation calculations are on file across four independent optimisation chains")
        expect(story).toContain("three of those calculations are a coarse pass later refined")
    })

    // Finding 1 of the BLOCK review: the per-observation branch had no
    // equivalent of the aggregate branch's `rawOptCount === 0` guard. A
    // conformer whose loaded observations carry freq/sp but no opt at all
    // satisfies the safety condition (chainCount 0 === coverageOpt 0, every
    // observation loaded) and used to fall through to an EMPTY `buckets`
    // map, producing a stray "." sentence fragment.
    it("says plainly that no observation has an optimization calculation, on the per-observation path, rather than a stray '.'", () => {
        const conformer = group({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 2, optimization_chain_count: 0, geometry_count: 0,
                evidence_coverage: { opt: 0, freq: 2, sp: 0 }, levels_of_theory: {},
            },
            observations: [
                { conformer_observation: { conformer_observation_ref: "co_a" }, calculations: [{ calculation_ref: "co_a_freq", type: "freq" }] },
                { conformer_observation: { conformer_observation_ref: "co_b" }, calculations: [{ calculation_ref: "co_b_freq", type: "freq" }] },
            ] as ConformerProjection["observations"],
            calculations: [
                { calculation_ref: "co_a_freq", type: "freq" }, { calculation_ref: "co_b_freq", type: "freq" },
            ] as ConformerProjection["calculations"],
        })
        // Confirm this fixture actually reaches the per-observation path --
        // otherwise this test would pass for the wrong reason.
        expect(optimizationStaging(conformer).kind).toBe("per-observation")
        const story = describeConformerEvidence(conformer)
        expect(story).not.toMatch(/\.\s*\.\s/) // no stray empty sentence
        expect(story).toContain("None of them have an optimisation calculation on file.")
        expect(story).toBe(
            "This conformer was sighted twice. None of them have an optimisation calculation on file. "
            + "Every sighting got a frequency calculation. None of the sightings got a single-point energy.",
        )
    })

    // Finding 2: the staging clause silently dropped sightings with no opt
    // row -- `perObservation` only gets an entry when `optCount > 0`, and
    // the old clause text carried no denominator, so 4 sightings with only
    // 2 having opt printed a staging clause that read as a complete
    // accounting of all 4 (the freq/sp sentences right beside it DO carry a
    // "of the four" denominator). This fixture is untested territory per
    // the review: no existing fixture had an opt-free observation on the
    // per-observation path.
    it("names how many sightings had NO optimisation on file, on the per-observation path, rather than silently dropping them", () => {
        const conformer = group({
            observations_summary: { total: 4 },
            evidence_summary: {
                calculation_count: 4, optimization_chain_count: 2, geometry_count: 1,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            observations: [
                { conformer_observation: { conformer_observation_ref: "co_a" }, calculations: [{ calculation_ref: "co_a_opt1", type: "opt" }, { calculation_ref: "co_a_opt2", type: "opt" }] },
                { conformer_observation: { conformer_observation_ref: "co_b" }, calculations: [{ calculation_ref: "co_b_opt", type: "opt" }] },
                { conformer_observation: { conformer_observation_ref: "co_c" }, calculations: [] },
                { conformer_observation: { conformer_observation_ref: "co_d" }, calculations: [] },
            ] as ConformerProjection["observations"],
            calculations: [
                { calculation_ref: "co_a_opt1", type: "opt" }, { calculation_ref: "co_a_opt2", type: "opt" }, { calculation_ref: "co_b_opt", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        expect(optimizationStaging(conformer).kind).toBe("per-observation")
        const story = describeConformerEvidence(conformer)
        // Exact string: without the denominator fix, this reads
        // "...single pass. A staged..." (period right after "single pass",
        // silently dropping co_c/co_d) instead of "...single pass, and two
        // had no optimisation on file. A staged...".
        expect(story).toBe(
            "This conformer was sighted four times. One was optimised in two stages, one in a single pass, "
            + "and two had no optimisation on file. A staged optimisation runs a coarse pass first, then "
            + "refines it. None of the sightings got a frequency calculation. None of the sightings got a "
            + "single-point energy.",
        )
    })

})
