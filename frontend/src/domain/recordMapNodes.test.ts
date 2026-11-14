import { describe, expect, it } from "vitest"
import {
    calculationNodes,
    networkNodes,
    conformerGroupNodes,
    conformerObservationNodes,
    geometryNodes,
    levelOfTheoryNodes,
    reactionOverviewNodes,
    transitionStateEntryNodes,
} from "./recordMapNodes"

const ids = (nodes: { id: string }[]) => nodes.map((node) => node.id)
const byId = <T extends { id: string }>(nodes: T[], id: string) => nodes.find((node) => node.id === id)

const participant = (ref: string, formula: string | null = null) => ({ entryRef: ref, formula, smiles: null })

describe("calculationNodes", () => {
    const base = {
        owner: { kind: "species_entry" as const, entryRef: "spe_1", formula: "CH3" },
        conformer: { groupRef: "cg_1", groupName: "Conformer Group 2", observationRef: "co_1" },
        inputGeometryRefs: ["geom_in"],
        outputGeometryRefs: ["geom_out", "geom_out2"],
        dependencies: [
            { direction: "child", parent_calculation_ref: "calc_p", child_calculation_ref: "calc_self" },
            { direction: "parent", parent_calculation_ref: "calc_self", child_calculation_ref: "calc_c1" },
            { direction: "parent", parent_calculation_ref: "calc_self", child_calculation_ref: "calc_c2" },
            { direction: "parent", parent_calculation_ref: "calc_self", child_calculation_ref: "calc_c2" },
        ],
        levelOfTheory: { ref: "lot_1", label: "b3lyp/def2tzvp" },
    }

    it("names every neighbour the page knows, kind first", () => {
        const nodes = calculationNodes(base)
        expect(ids(nodes)).toEqual(["owner", "conformer-group", "conformer-observation", "geometry", "related", "level-of-theory"])
        expect(nodes.map((n) => n.label)).toEqual([
            "Molecule version", "Conformer group", "Conformer observation", "Geometries", "Calculations it links to", "Level of theory",
        ])
        expect(byId(nodes, "owner")).toMatchObject({ to: "/species-entries/spe_1", sub: "CH3" })
        expect(byId(nodes, "level-of-theory")).toMatchObject({ to: "/methods/lot_1", sub: "b3lyp/def2tzvp" })
    })

    it("counts distinct related calculations and links to the page's own sections", () => {
        const nodes = calculationNodes(base)
        expect(byId(nodes, "related")).toMatchObject({
            sub: "depends\u00a0on\u00a01, used\u00a0by\u00a02", to: "#dependencies-heading", relation: "Listed under Depends on and Used by",
        })
        const onlyChildren = calculationNodes({ ...base, dependencies: base.dependencies.filter((d) => d.direction === "parent") })
        expect(byId(onlyChildren, "related")).toMatchObject({ to: "#used-by-heading", relation: "Listed under Used by" })
        expect(byId(nodes, "geometry")).toMatchObject({ sub: "1 input, 2 output", to: "#geometries-heading" })
    })

    it("links a single geometry directly and says which role it has", () => {
        const input = { ...base, inputGeometryRefs: ["geom_x"], outputGeometryRefs: ["geom_x"] }
        // The same row in both lists is the output, as the Geometries section says, unless the start was extracted from the input file.
        expect(byId(calculationNodes(input), "geometry")).toMatchObject({ label: "Geometry", sub: "output", to: "/geometries/geom_x" })
        expect(byId(calculationNodes({ ...input, inputExtractedFromArtifact: true }), "geometry")?.sub).toBe("input and output")
        expect(byId(calculationNodes({ ...input, outputGeometryRefs: [] }), "geometry")?.sub).toBe("input")
    })

    it("leaves out what the page does not hold", () => {
        const nodes = calculationNodes({
            owner: null, conformer: null, inputGeometryRefs: null, outputGeometryRefs: [], dependencies: [], levelOfTheory: null,
        })
        expect(nodes).toEqual([])
    })

    it("names the conformer group by its ref when it has no archive number", () => {
        const nodes = calculationNodes({ ...base, conformer: { groupRef: "cg_9", groupName: null, observationRef: "co_9" } })
        expect(byId(nodes, "conformer-group")).toMatchObject({ ref: "cg_9" })
        expect(byId(nodes, "conformer-group")?.sub).toBeUndefined()
    })

    it("adds the reaction version for a transition-state owner", () => {
        const nodes = calculationNodes({
            ...base, owner: { kind: "transition_state_entry", entryRef: "tse_1", reactionEntryRef: "rxe_1" }, conformer: null,
        })
        expect(byId(nodes, "owner")).toMatchObject({ label: "Transition state", kind: "transition-state", to: "/transition-state-entries/tse_1" })
        expect(byId(nodes, "reaction-entry")).toMatchObject({ label: "Reaction version", to: "/reaction-entries/rxe_1" })
        const withoutReaction = calculationNodes({ ...base, owner: { kind: "transition_state_entry", entryRef: "tse_1", reactionEntryRef: null } })
        expect(byId(withoutReaction, "reaction-entry")).toBeUndefined()
    })
})

describe("conformerGroupNodes", () => {
    const base = {
        entry: { ref: "spe_1", formula: "CH3" },
        observationCount: 3, onlyObservationRef: null, calculationCount: 7, geometryCount: 4, thermoCount: 2, kineticsCount: 1,
    }

    it("counts what the group holds and what uses it, linking to its own sections", () => {
        const nodes = conformerGroupNodes(base)
        expect(ids(nodes)).toEqual(["entry", "observations", "calculations", "geometries", "thermo", "kinetics"])
        expect(byId(nodes, "observations")).toMatchObject({ found: "3 found", to: "#observation-ledger" })
        expect(byId(nodes, "geometries")).toMatchObject({ found: "4 found", to: "#geometry-ledger" })
        expect(byId(nodes, "thermo")).toMatchObject({ found: "2 found", to: "#used-by-heading" })
        expect(byId(nodes, "kinetics")).toMatchObject({ found: "1 found", to: "#used-by-heading" })
    })

    it("links the only observation directly", () => {
        const nodes = conformerGroupNodes({ ...base, observationCount: 1, onlyObservationRef: "co_1" })
        expect(byId(nodes, "observations")?.to).toBe("/conformer-observations/co_1")
    })

    it("leaves out use counts until they are read, and zero counts always", () => {
        expect(ids(conformerGroupNodes({ ...base, thermoCount: null, kineticsCount: null }))).not.toContain("thermo")
        const empty = conformerGroupNodes({ ...base, observationCount: 0, calculationCount: 0, geometryCount: 0, thermoCount: 0, kineticsCount: 0 })
        expect(ids(empty)).toEqual(["entry"])
    })
})

describe("conformerObservationNodes", () => {
    it("names the group, molecule version, calculations and geometries", () => {
        const nodes = conformerObservationNodes({
            group: { ref: "cg_1", name: "Conformer Group 1" }, entry: { ref: "spe_1", formula: "CH3" }, calculationCount: 2, geometryCount: 1,
        })
        expect(ids(nodes)).toEqual(["group", "entry", "calculations", "geometries"])
        expect(byId(nodes, "group")).toMatchObject({ to: "/conformer-groups/cg_1", sub: "Conformer Group 1" })
        expect(byId(nodes, "calculations")).toMatchObject({ found: "2 found", to: "#calc-ledger" })
    })

    it("leaves out a hidden group and empty counts", () => {
        const nodes = conformerObservationNodes({ group: null, entry: { ref: "spe_1", formula: null }, calculationCount: 0, geometryCount: 0 })
        expect(ids(nodes)).toEqual(["entry"])
    })
})

describe("geometryNodes", () => {
    const typeLabel = (type: string) => (type === "opt" ? "Optimisation" : type)

    it("links a single producing calculation and counts several consumers, each named apart", () => {
        const nodes = geometryNodes({
            owner: { kind: "species_entry", entryRef: "spe_1", formula: "CH3" },
            producedBy: [{ calculation_ref: "calc_1", calculation_type: "opt" }],
            usedAsInputBy: [{ calculation_ref: "calc_2", calculation_type: "freq" }, { calculation_ref: "calc_3", calculation_type: "sp" }],
            typeLabel,
        })
        expect(ids(nodes)).toEqual(["owner", "produced-by", "used-by"])
        expect(byId(nodes, "produced-by")).toMatchObject({ label: "Producing calculation", to: "/calculations/calc_1", sub: "Optimisation" })
        expect(byId(nodes, "used-by")).toMatchObject({ label: "Calculations using it", found: "2 found", to: "#provenance-used-as-input-by" })
        expect(byId(nodes, "used-by")?.sub).toBeUndefined()
    })

    it("knows no owner when the geometry is shared, and nothing when the lists were not served", () => {
        expect(geometryNodes({ owner: null, producedBy: null, usedAsInputBy: null, typeLabel })).toEqual([])
        expect(geometryNodes({ owner: null, producedBy: [], usedAsInputBy: [], typeLabel })).toEqual([])
    })

    it("names a transition-state owner", () => {
        const nodes = geometryNodes({ owner: { kind: "transition_state_entry", entryRef: "tse_1" }, producedBy: null, usedAsInputBy: null, typeLabel })
        expect(byId(nodes, "owner")).toMatchObject({ kind: "transition-state", to: "/transition-state-entries/tse_1" })
    })
})

describe("transitionStateEntryNodes", () => {
    it("lists the reaction version, one molecule version per side, how many more, and the calculation count", () => {
        const nodes = transitionStateEntryNodes({
            reactionEntryRef: "rxe_1", reactants: [participant("spe_a", "CH4"), participant("spe_b", "OH")], products: [participant("spe_c", "CH3")],
            calculationCount: 5,
        })
        expect(ids(nodes)).toEqual(["reaction-entry", "reactant-0", "product-0", "participants-more", "calculations"])
        expect(byId(nodes, "participants-more")).toMatchObject({ label: "1 more molecule version", to: "/reaction-entries/rxe_1" })
        expect(byId(nodes, "calculations")).toMatchObject({ label: "Calculations", found: "5 found", to: "#calc-ledger" })
        expect(byId(transitionStateEntryNodes({ reactionEntryRef: null, reactants: [], products: [], calculationCount: 1 }), "calculations")?.label).toBe("Calculation")
    })

    it("leaves out the reaction version and its links when the entry names none", () => {
        const nodes = transitionStateEntryNodes({ reactionEntryRef: null, reactants: [], products: [], calculationCount: 0 })
        expect(nodes).toEqual([])
    })

    it("gives a shared formula its SMILES as the second line", () => {
        const nodes = transitionStateEntryNodes({
            reactionEntryRef: "rxe_1",
            reactants: [{ entryRef: "a", formula: "C3H5", smiles: "[CH2]C=C" }], products: [{ entryRef: "b", formula: "C3H5", smiles: "[CH]1CC1" }],
            calculationCount: 0,
        })
        expect(byId(nodes, "reactant-0")?.sub).toBe("[CH2]C=C")
        expect(byId(nodes, "reactant-0")?.drawnNote).toBe("Reactant · [CH2]C=C")
        expect(byId(nodes, "product-0")?.sub).toBe("[CH]1CC1")
    })
})

describe("reactionOverviewNodes", () => {
    it("shows two entries and says how many more are listed under Reaction entries", () => {
        const nodes = reactionOverviewNodes({
            entries: [{ ref: "rxe_1", position: 61 }, { ref: "rxe_2", position: 60 }, { ref: "rxe_3", position: 59 }], total: 61, reactants: [participant("spe_a", "CH4")], products: [participant("spe_b", "CH3")],
        })
        expect(ids(nodes)).toEqual(["rxe_1", "rxe_2", "more-entries", "reactant-0", "product-0"])
        expect(nodes.slice(0, 2).map((n) => n.label)).toEqual(["Version 61", "Version 60"])
        expect(byId(nodes, "reactant-0")?.relation).toBe("Reactant, Version 61")
        expect(byId(nodes, "more-entries")).toMatchObject({ label: "59 more under Reaction entries", to: "#entries-heading" })
    })

    it("adds no more-row when every entry is shown", () => {
        const nodes = reactionOverviewNodes({ entries: [{ ref: "rxe_1", position: 1 }], total: 1, reactants: [], products: [] })
        expect(ids(nodes)).toEqual(["rxe_1"])
    })

    it("keeps one molecule version per side and one row for the rest, at most six nodes", () => {
        const nodes = reactionOverviewNodes({
            entries: [{ ref: "rxe_1", position: 1 }, { ref: "rxe_2", position: 2 }], total: 9, reactants: [participant("a"), participant("b"), participant("c")], products: [participant("d"), participant("e")],
        })
        expect(byId(nodes, "participants-more")).toMatchObject({ label: "3 more molecule versions", relation: "Listed on Version 1", to: "/reaction-entries/rxe_1" })
        expect(nodes.length).toBeLessThanOrEqual(6)
    })
})

describe("levelOfTheoryNodes", () => {
    it("counts calculations and deposited corrections, and links the composite recipe", () => {
        const nodes = levelOfTheoryNodes({ calculationCount: 12, compositeScheme: { ref: "cs_1", label: "extrapolation" }, schemeCount: 2, scaleFactorCount: 3 })
        expect(ids(nodes)).toEqual(["calculations", "composite", "schemes", "scale-factors"])
        expect(byId(nodes, "calculations")).toMatchObject({ found: "12 found", to: "#lot-usage-heading" })
        expect(byId(nodes, "composite")?.to).toBe("/methods/composite-schemes/cs_1")
    })

    it("leaves out what is absent or zero", () => {
        expect(levelOfTheoryNodes({ calculationCount: 0, compositeScheme: null, schemeCount: null, scaleFactorCount: 0 })).toEqual([])
    })
})

describe("labels", () => {
    it("are singular for one record", () => {
        const nodes = conformerGroupNodes({
            entry: { ref: "spe_1", formula: null }, observationCount: 1, onlyObservationRef: null, calculationCount: 1, geometryCount: 1, thermoCount: 1, kineticsCount: 1,
        })
        expect(nodes.map((n) => n.label)).toEqual(["Molecule version", "Conformer observation", "Calculation", "Geometry", "Thermochemistry", "Rate record"])
    })
})

describe("networkNodes", () => {
    it("counts the molecule versions, reaction versions and solves the page loaded, linking its own sections", () => {
        const nodes = networkNodes({ speciesEntryRefs: ["a", "b", "c"], reactionEntryRefs: ["r1", "r2"], solveCount: 1 })
        expect(nodes.map((n) => [n.label, n.found, n.to])).toEqual([
            ["Molecule versions", "3 found", "#evidence-heading"],
            ["Reaction versions", "2 found", "#reactions-heading"],
            ["Network solve", "1 found", "#evidence-heading"],
        ])
    })

    it("links a lone molecule or reaction version and leaves out what is absent", () => {
        const nodes = networkNodes({ speciesEntryRefs: ["a"], reactionEntryRefs: ["r1"], solveCount: 0 })
        expect(nodes.map((n) => n.to)).toEqual(["/species-entries/a", "/reaction-entries/r1"])
        expect(networkNodes({ speciesEntryRefs: [], reactionEntryRefs: [], solveCount: 0 })).toEqual([])
    })
})
