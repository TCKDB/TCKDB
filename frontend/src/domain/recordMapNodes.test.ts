import { describe, expect, it } from "vitest"
import {
    calculationNodes,
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
            "Molecule version", "Conformer group", "Conformer observation", "Geometries", "Related calculations", "Level of theory",
        ])
        expect(byId(nodes, "owner")).toMatchObject({ to: "/species-entries/spe_1", sub: "CH3" })
        expect(byId(nodes, "level-of-theory")).toMatchObject({ to: "/methods/lot_1", sub: "b3lyp/def2tzvp" })
    })

    it("counts distinct related calculations and links to the page's own sections", () => {
        const nodes = calculationNodes(base)
        expect(byId(nodes, "related")).toMatchObject({ sub: "depends on 1, used by 2", to: "#dependencies-heading" })
        expect(byId(nodes, "geometry")).toMatchObject({ sub: "1 input, 2 output", to: "#geometries-heading" })
    })

    it("links a single geometry directly and says which role it has", () => {
        const nodes = calculationNodes({ ...base, inputGeometryRefs: ["geom_x"], outputGeometryRefs: ["geom_x"] })
        expect(byId(nodes, "geometry")).toMatchObject({ label: "Geometry", sub: "input and output", to: "/geometries/geom_x" })
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

    it("links a single producing calculation and counts several consumers", () => {
        const nodes = geometryNodes({
            owner: { kind: "species_entry", entryRef: "spe_1", formula: "CH3" },
            producedBy: [{ calculation_ref: "calc_1", calculation_type: "opt" }],
            usedAsInputBy: [{ calculation_ref: "calc_2", calculation_type: "freq" }, { calculation_ref: "calc_3", calculation_type: "sp" }],
            typeLabel,
        })
        expect(ids(nodes)).toEqual(["owner", "produced-by", "used-by"])
        expect(byId(nodes, "produced-by")).toMatchObject({ to: "/calculations/calc_1", sub: "Optimisation, produced it" })
        expect(byId(nodes, "used-by")).toMatchObject({ sub: "2 found, takes it as input", to: "#provenance-used-as-input-by" })
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
    it("lists the reaction version, both sides and the calculation count", () => {
        const nodes = transitionStateEntryNodes({
            reactionEntryRef: "rxe_1", reactants: [participant("spe_a", "CH4"), participant("spe_b", "OH")], products: [participant("spe_c", "CH3")],
            calculationCount: 5,
        })
        expect(ids(nodes)).toEqual(["reaction-entry", "reactant-0", "reactant-1", "product-0", "calculations"])
        expect(byId(nodes, "calculations")).toMatchObject({ found: "5 found", to: "#calc-ledger" })
    })

    it("says how many more sit on the reaction version when a side is long", () => {
        const nodes = transitionStateEntryNodes({
            reactionEntryRef: "rxe_1", reactants: [participant("a"), participant("b"), participant("c"), participant("d")], products: [],
            calculationCount: 0,
        })
        expect(byId(nodes, "reactant-more")).toMatchObject({ label: "2 more reactants", to: "/reaction-entries/rxe_1" })
        expect(ids(nodes)).not.toContain("calculations")
    })

    it("leaves out the reaction version and its links when the entry names none", () => {
        const nodes = transitionStateEntryNodes({ reactionEntryRef: null, reactants: [], products: [], calculationCount: 0 })
        expect(nodes).toEqual([])
    })
})

describe("reactionOverviewNodes", () => {
    it("shows two entries and says how many more are listed under Reaction entries", () => {
        const nodes = reactionOverviewNodes({
            entryRefs: ["rxe_1", "rxe_2", "rxe_3"], total: 5, reactants: [participant("spe_a", "CH4")], products: [participant("spe_b", "CH3")],
        })
        expect(ids(nodes)).toEqual(["rxe_1", "rxe_2", "more-entries", "reactant-0", "product-0"])
        expect(byId(nodes, "more-entries")).toMatchObject({ label: "3 more under Reaction entries", to: "#entries-heading" })
    })

    it("adds no more-row when every entry is shown", () => {
        const nodes = reactionOverviewNodes({ entryRefs: ["rxe_1"], total: 1, reactants: [], products: [] })
        expect(ids(nodes)).toEqual(["rxe_1"])
    })

    it("caps each side and points the rest at the first reaction version", () => {
        const nodes = reactionOverviewNodes({
            entryRefs: ["rxe_1"], total: 1, reactants: [participant("a"), participant("b"), participant("c")], products: [],
        })
        expect(byId(nodes, "reactant-more")).toMatchObject({ label: "1 more reactant", to: "/reaction-entries/rxe_1" })
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
