import { describe, expect, it } from "vitest"
import { speciesEntryNodes, reactionEntryNodes, type NeighbourNode, type SpeciesEntryProbes } from "./neighbourNodes"

const probe = (total: number, row: Record<string, unknown> | null = null) => ({ total, row })
const probes = (over: Partial<SpeciesEntryProbes>): SpeciesEntryProbes => ({
    molecule: null, conformers: null, calculations: null, networks: null, reactions: null, ...over,
})
const byId = (nodes: NeighbourNode[], id: string) => nodes.find((node) => node.id === id)

const several: SpeciesEntryProbes = probes({
    molecule: probe(1, { species_ref: "spc_1" }),
    conformers: probe(3, { conformer_group: { conformer_group_ref: "cg_1" } }),
    calculations: probe(4),
    networks: probe(2, { network: { network_ref: "net_1" } }),
    reactions: probe(5, { reaction_entry_ref: "rxe_1" }),
})

describe("speciesEntryNodes", () => {
    it("links every node of a species entry with several records of each kind, except networks (listed nowhere)", () => {
        const nodes = speciesEntryNodes("spe_1", several, new Set(), "[CH2]C=C")
        expect(nodes.map((node) => node.id)).toEqual(["molecule", "conformers", "calculations", "networks", "reactions"])
        for (const node of nodes) {
            if (node.id === "networks") continue
            expect(node.to, node.id).not.toBeNull()
        }
    })

    it("opens the shape calculations section for the calculations, and says they are listed by shape", () => {
        const node = byId(speciesEntryNodes("spe_1", several, new Set(), null), "calculations")!
        expect(node).toMatchObject({ to: "#shape-calculations", found: "4 found, listed by shape" })
        // The entry page renders that id under its selected shape (see "keeps a link fragment" in SpeciesEntryPage.test.tsx).
    })

    it("leaves calculations as text when the entry has no shape to list them under", () => {
        const node = byId(speciesEntryNodes("spe_1", probes({ calculations: probe(4) }), new Set(), null), "calculations")!
        expect(node).toMatchObject({ to: null, found: "4 found, not listed yet" })
    })

    it("opens the reactions listing filtered to this molecule when several reaction entries have it", () => {
        const node = byId(speciesEntryNodes("spe_1", several, new Set(), "[CH2]C=C"), "reactions")!
        expect(node.to).toBe("/reactions?reactant_smiles=%5BCH2%5DC%3DC&direction=either")
        expect(node.found).toBe("5 reaction entries")
    })

    it("opens the one reaction entry, network and conformer group when each is alone", () => {
        const nodes = speciesEntryNodes("spe_1", probes({
            conformers: probe(1, { conformer_group: { conformer_group_ref: "cg_1" } }),
            networks: probe(1, { network: { network_ref: "net_1" } }),
            reactions: probe(1, { reaction_entry_ref: "rxe_1" }),
        }), new Set(), "C")
        expect(byId(nodes, "conformers")?.to).toBe("/conformer-groups/cg_1")
        expect(byId(nodes, "networks")?.to).toBe("/networks/net_1")
        expect(byId(nodes, "reactions")?.to).toBe("/reaction-entries/rxe_1")
    })

    it("leaves several networks as text with a visible hint, because no page lists them", () => {
        const node = byId(speciesEntryNodes("spe_1", several, new Set(), "C"), "networks")!
        expect(node.to).toBeNull()
        expect(node.found).toBe("2 found, not listed yet")
    })

    it("keeps a failed read as text saying it is not available", () => {
        const nodes = speciesEntryNodes("spe_1", probes({}), new Set(["calculations"] as const))
        expect(byId(nodes, "calculations")).toMatchObject({ to: null, found: "Not available right now" })
    })
})

describe("reactionEntryNodes", () => {
    it("links every participant and the reaction", () => {
        const nodes = reactionEntryNodes({
            reactionRef: "rxn_1", reactants: [{ entryRef: "spe_1" }], products: [{ entryRef: "spe_2" }],
        })
        expect(nodes).toHaveLength(3)
        for (const node of nodes) expect(node.to, node.id).not.toBeNull()
    })
})
