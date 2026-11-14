import { describe, expect, it } from "vitest"
import {
    calculationNodes,
    conformerGroupNodes,
    conformerObservationNodes,
    geometryNodes,
    levelOfTheoryNodes,
    networkNodes,
    reactionOverviewNodes,
    transitionStateEntryNodes,
} from "./recordMapNodes"
import { speciesEntryNodes } from "./neighbourNodes"
import calculationPage from "../pages/CalculationDetailPage.tsx?raw"
import conformerGroupPage from "../pages/ConformerGroupPage.tsx?raw"
import conformerObservationPage from "../pages/ConformerObservationPage.tsx?raw"
import geometryPage from "../pages/GeometryDetailPage.tsx?raw"
import levelOfTheoryPage from "../pages/LevelOfTheoryPage.tsx?raw"
import networkPage from "../pages/NetworkEntryPage.tsx?raw"
import reactionOverviewPage from "../pages/ReactionOverviewPage.tsx?raw"
import transitionStateEntryPage from "../pages/TransitionStateEntryPage.tsx?raw"

const p = (ref: string) => ({ entryRef: ref, formula: "X", smiles: null })
const many = ["a", "b", "c"]
const calcs = [{ calculation_ref: "c1", calculation_type: "opt" }, { calculation_ref: "c2", calculation_type: "opt" }]

type Case = { nodes: ReturnType<typeof calculationNodes>; source: string }
const all: Record<string, Case> = {
    calculation: {
        source: calculationPage,
        nodes: calculationNodes({
            owner: { kind: "transition_state_entry", entryRef: "tse_1", reactionEntryRef: "rxe_1" },
            conformer: { groupRef: "cg_1", groupName: null, observationRef: "co_1" },
            inputGeometryRefs: ["g1"], outputGeometryRefs: ["g2", "g3"],
            dependencies: [{ direction: "child", parent_calculation_ref: "c1", child_calculation_ref: "c2" }],
            levelOfTheory: { ref: "lot_1", label: "x" },
        }),
    },
    conformerGroup: {
        source: conformerGroupPage,
        nodes: conformerGroupNodes({
            entry: { ref: "spe_1", formula: null }, observationCount: 3, onlyObservationRef: null, calculationCount: 3,
            geometryCount: 3, thermoCount: 2, kineticsCount: 2,
        }),
    },
    conformerObservation: {
        source: conformerObservationPage,
        nodes: conformerObservationNodes({
            group: { ref: "cg_1", name: null }, entry: { ref: "spe_1", formula: null }, calculationCount: 3, geometryCount: 3,
        }),
    },
    geometry: {
        source: geometryPage,
        nodes: geometryNodes({
            owner: { kind: "species_entry", entryRef: "spe_1", formula: null }, typeLabel: (type) => type,
            producedBy: calcs, usedAsInputBy: calcs,
        }),
    },
    transitionStateEntry: {
        source: transitionStateEntryPage,
        nodes: transitionStateEntryNodes({
            reactionEntryRef: "rxe_1", reactants: [p("a"), p("b")], products: [p("c"), p("d")], calculationCount: 3,
        }),
    },
    reactionOverview: {
        source: reactionOverviewPage,
        nodes: reactionOverviewNodes({ entries: many.map((ref, index) => ({ ref, position: index + 1 })), total: 5, reactants: [p("a"), p("b")], products: [p("c"), p("d")] }),
    },
    levelOfTheory: {
        source: levelOfTheoryPage,
        nodes: levelOfTheoryNodes({
            calculationCount: 3, compositeScheme: { ref: "cs_1", label: "x" }, schemeCount: 2, scaleFactorCount: 2,
        }),
    },
    network: { source: networkPage, nodes: networkNodes({ speciesEntryRefs: many, reactionEntryRefs: many, solveCount: 3 }) },
}

describe("every map node opens something", () => {
    it("a single network links to that network", () => {
        const nodes = speciesEntryNodes("spe_1", {
            molecule: null, conformers: null, calculations: null, reactions: null,
            networks: { total: 1, row: { network: { network_ref: "net_1" } } },
        })
        expect(nodes).toHaveLength(1)
        expect(nodes[0]).toMatchObject({ id: "networks", to: "/networks/net_1" })
    })

    it.each(Object.keys(all))("%s: no node is plain text", (name) => {
        expect(all[name].nodes.length).toBeGreaterThan(0)
        for (const node of all[name].nodes) expect(node.to, `${name}/${node.id}`).not.toBeNull()
    })

    it("a level of theory without a page, or a reaction version the entry does not name, is text that says so", () => {
        const lot = calculationNodes({
            owner: null, conformer: null, inputGeometryRefs: null, outputGeometryRefs: null, dependencies: null,
            levelOfTheory: { ref: null, label: "x" },
        })
        expect(lot[0]).toMatchObject({ to: null, found: "no page yet" })
        const ts = transitionStateEntryNodes({ reactionEntryRef: null, reactants: [p("a"), p("b")], products: [], calculationCount: 0 })
        expect(ts.find((node) => node.id === "participants-more")).toMatchObject({ to: null, found: "not listed yet" })
    })

    it("every anchor a node opens exists on the page that owns the map", () => {
        const withoutComments = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/.*$/gm, "$1")
        // The geometry page derives its section ids from the section titles; read the titles from the source.
        const slug = (title: string) => `provenance-${title.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
        const provenanceIds = (source: string) => [...source.matchAll(/title="([^"]+)"/g)].map((match) => slug(match[1]))
        let checked = 0
        for (const [name, { nodes, source }] of Object.entries(all)) {
            for (const node of nodes) {
                if (!node.to?.startsWith("#")) continue
                const id = node.to.slice(1)
                const code = withoutComments(source)
                const present = code.includes(`id="${id}"`) || (name === "geometry" && provenanceIds(code).includes(id))
                expect(present, `${name}/${node.id} -> ${node.to}`).toBe(true)
                checked++
            }
        }
        expect(checked).toBeGreaterThan(10)
    })
})
