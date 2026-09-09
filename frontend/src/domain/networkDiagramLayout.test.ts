import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import {
    computeNetworkDiagramLayout,
    NETWORK_DIAGRAM_HEIGHT,
    NETWORK_DIAGRAM_NODE_THRESHOLD,
    NETWORK_DIAGRAM_WIDTH,
    shouldDegradeNetworkDiagram,
} from "./networkDiagramLayout"

function state(hash: string, kind: string, label: string): NetworkState {
    return {
        composition_hash: hash,
        kind,
        label: null,
        participant_count: 1,
        composition: { participants: [], participant_count_total: 1, participants_truncated: false, state_label: label },
    }
}

function channel(key: string, source: string, sink: string): NetworkChannel {
    return {
        channel_key: key,
        kind: "association",
        mechanism: "elementary",
        source_state_composition_hash: source,
        sink_state_composition_hash: sink,
        has_kinetics: true,
        microreactions: [],
    }
}

const STATES: NetworkState[] = [
    state("h1", "well", "NN"),
    state("h2", "well", "[NH-][NH3+]"),
    state("h3", "bimolecular", "N=N (Z) + [H][H]"),
    state("h4", "bimolecular", "[H][H] + [N-]=[NH2+]"),
    state("h5", "bimolecular", "[H] + [NH]N"),
    state("h6", "bimolecular", "2 [NH2]"),
    state("h7", "bimolecular", "N=N (E) + [H][H]"),
]

const CHANNELS: NetworkChannel[] = [
    channel("channel_1", "h2", "h1"),
    channel("channel_2", "h3", "h1"),
    channel("channel_3", "h4", "h1"),
]

describe("computeNetworkDiagramLayout -- determinism", () => {
    it("produces identical positions for identical input, computed twice", () => {
        const a = computeNetworkDiagramLayout(STATES, CHANNELS)
        const b = computeNetworkDiagramLayout(STATES, CHANNELS)
        expect(a).toEqual(b)
    })

    it("every node lands within the canvas bounds", () => {
        const { nodes } = computeNetworkDiagramLayout(STATES, CHANNELS)
        for (const node of nodes) {
            expect(node.x).toBeGreaterThanOrEqual(0)
            expect(node.x).toBeLessThanOrEqual(NETWORK_DIAGRAM_WIDTH)
            expect(node.y).toBeGreaterThanOrEqual(0)
            expect(node.y).toBeLessThanOrEqual(NETWORK_DIAGRAM_HEIGHT)
        }
    })

    it("no two nodes land on the exact same point (mutual repulsion actually ran)", () => {
        const { nodes } = computeNetworkDiagramLayout(STATES, CHANNELS)
        const points = new Set(nodes.map((n) => `${n.x},${n.y}`))
        expect(points.size).toBe(nodes.length)
    })
})

describe("computeNetworkDiagramLayout -- node/edge shape", () => {
    it("returns one node per state, carrying composition_hash, state_label and isWell", () => {
        const { nodes } = computeNetworkDiagramLayout(STATES, CHANNELS)
        expect(nodes).toHaveLength(STATES.length)
        const well = nodes.find((n) => n.compositionHash === "h1")!
        expect(well.label).toBe("NN")
        expect(well.isWell).toBe(true)
        const bimolecular = nodes.find((n) => n.compositionHash === "h3")!
        expect(bimolecular.isWell).toBe(false)
    })

    it("returns one edge per channel, carrying channelKey/kind/hasKinetics and both endpoints' coordinates", () => {
        const { edges } = computeNetworkDiagramLayout(STATES, CHANNELS)
        expect(edges).toHaveLength(CHANNELS.length)
        const edge = edges.find((e) => e.channelKey === "channel_1")!
        expect(edge.sourceHash).toBe("h2")
        expect(edge.sinkHash).toBe("h1")
        expect(Number.isFinite(edge.x1)).toBe(true)
        expect(Number.isFinite(edge.y1)).toBe(true)
    })

    it("skips a channel whose source or sink hash is absent from states[] rather than throwing", () => {
        const dangling = [...CHANNELS, channel("channel_dangling", "h1", "h_missing")]
        const { edges } = computeNetworkDiagramLayout(STATES, dangling)
        expect(edges).toHaveLength(CHANNELS.length)
        expect(edges.some((e) => e.channelKey === "channel_dangling")).toBe(false)
    })

    it("returns empty layout for zero states", () => {
        expect(computeNetworkDiagramLayout([], [])).toEqual({ nodes: [], edges: [] })
    })
})

describe("shouldDegradeNetworkDiagram -- the chosen threshold", () => {
    it("does not degrade the live archive's own shape (7 states, 21 channels)", () => {
        expect(shouldDegradeNetworkDiagram(7, 21)).toBe(false)
    })

    it(`does not degrade exactly at the threshold (${NETWORK_DIAGRAM_NODE_THRESHOLD} states)`, () => {
        expect(shouldDegradeNetworkDiagram(NETWORK_DIAGRAM_NODE_THRESHOLD, 10)).toBe(false)
    })

    it(`degrades one state past the threshold (${NETWORK_DIAGRAM_NODE_THRESHOLD + 1} states)`, () => {
        expect(shouldDegradeNetworkDiagram(NETWORK_DIAGRAM_NODE_THRESHOLD + 1, 10)).toBe(true)
    })

    it("degrades a small but unusually dense network (denser than a complete graph at the threshold)", () => {
        const completeAtThreshold = (NETWORK_DIAGRAM_NODE_THRESHOLD * (NETWORK_DIAGRAM_NODE_THRESHOLD - 1)) / 2
        expect(shouldDegradeNetworkDiagram(10, completeAtThreshold + 1)).toBe(true)
    })
})
