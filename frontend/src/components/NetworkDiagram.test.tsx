import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import { NetworkDiagram } from "./NetworkDiagram"

afterEach(() => cleanup())

function state(hash: string, kind: string, label: string, participants: { ref: string; smiles: string; stoich?: number }[] = []): NetworkState {
    return {
        composition_hash: hash,
        kind,
        label: null,
        participant_count: participants.length,
        composition: {
            participants: participants.map((p) => ({
                species_entry_ref: p.ref,
                species_ref: `sp_${p.ref}`,
                canonical_smiles: p.smiles,
                stoichiometry: p.stoich ?? 1,
            })),
            participant_count_total: participants.length,
            participants_truncated: false,
            state_label: label,
        },
    }
}

function channel(key: string, kind: string, source: string, sink: string, hasKinetics = true, mechanism = "elementary"): NetworkChannel {
    return {
        channel_key: key,
        kind,
        mechanism,
        source_state_composition_hash: source,
        sink_state_composition_hash: sink,
        has_kinetics: hasKinetics,
        microreactions: [],
    }
}

const HYDRAZINE_STATES: NetworkState[] = [
    state("hash_n1", "well", "NN", [{ ref: "spe_nn", smiles: "NN" }]),
    state("hash_n2", "well", "[NH-][NH3+]", [{ ref: "spe_zw", smiles: "[NH-][NH3+]" }]),
    state("hash_n3", "bimolecular", "N=N (Z) + [H][H]", [{ ref: "spe_z", smiles: "N=N" }, { ref: "spe_h2", smiles: "[H][H]" }]),
    state("hash_n6", "bimolecular", "2 [NH2]", [{ ref: "spe_nh2", smiles: "[NH2]", stoich: 2 }]),
]

const HYDRAZINE_CHANNELS: NetworkChannel[] = [
    channel("channel_1", "isomerization", "hash_n2", "hash_n1", true),
    channel("channel_2", "association", "hash_n3", "hash_n1", true),
    channel("channel_5", "association", "hash_n6", "hash_n1", false),
]

function renderDiagram(states: NetworkState[], channels: NetworkChannel[]) {
    return render(
        <MemoryRouter>
            <NetworkDiagram states={states} channels={channels} />
        </MemoryRouter>,
    )
}

describe("NetworkDiagram -- node/edge counts match the served data", () => {
    it("renders exactly one node link per state and one edge link per channel", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(container.querySelectorAll(".net-node-link")).toHaveLength(HYDRAZINE_STATES.length)
        expect(container.querySelectorAll(".net-edge-link")).toHaveLength(HYDRAZINE_CHANNELS.length)
    })
})

describe("NetworkDiagram -- channel_key never renders as an SVG text label", () => {
    it("channel_1 appears as a data-channel-key attribute and in the table, never inside <text>", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const svgTexts = Array.from(container.querySelectorAll("svg text"))
        for (const text of svgTexts) {
            expect(text.textContent).not.toMatch(/channel_\d/)
        }
        const line = container.querySelector('line[data-channel-key="channel_1"]')
        expect(line).not.toBeNull()
        const cell = container.querySelector('td[data-label="Channel"] code.data')
        expect(Array.from(container.querySelectorAll('td[data-label="Channel"] code.data')).some((el) => el.textContent === "channel_1")).toBe(true)
        expect(cell).not.toBeNull()
    })
})

describe("NetworkDiagram -- every visible node label is composition.state_label", () => {
    it("renders each state's state_label as SVG text, never the composition_hash", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const svgTexts = Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)
        expect(svgTexts).toContain("NN")
        expect(svgTexts).toContain("2 [NH2]")
        // The raw hash legitimately appears in a `data-composition-hash`
        // attribute (a programmatic join key, never rendered as text) --
        // check rendered TEXT, not the serialised markup.
        expect(container.textContent ?? "").not.toContain("hash_n1")
    })
})

describe("NetworkDiagram -- node shape encodes kind, never inferred from label text", () => {
    it("a well state renders a <circle>, a bimolecular state renders a <polygon>", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        // Scoped to the actual diagram SVG: the legend also carries one
        // small illustrative `.net-node-well`/`.net-node-bimolecular` icon
        // each, which is not one of this fixture's real nodes.
        const svg = container.querySelector(".network-diagram-svg")!
        expect(svg.querySelectorAll(".net-node-well")).toHaveLength(2)
        expect(svg.querySelectorAll(".net-node-bimolecular")).toHaveLength(2)
        expect(svg.querySelector("circle.net-node-well")).not.toBeNull()
        expect(svg.querySelector("polygon.net-node-bimolecular")).not.toBeNull()
    })
})

describe("NetworkDiagram -- edge dash encodes has_kinetics", () => {
    it("a channel without kinetics renders a dashed line, channels with kinetics render solid", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const dashed = container.querySelector('line[data-channel-key="channel_5"]')
        const solid = container.querySelector('line[data-channel-key="channel_1"]')
        expect(dashed).toHaveAttribute("stroke-dasharray", "4 3")
        expect(solid).not.toHaveAttribute("stroke-dasharray")
    })
})

describe("NetworkDiagram -- the accessible table always renders, regardless of SVG state", () => {
    it("renders both state and channel tables alongside the SVG under threshold", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
    })

    it("renders both tables even when the diagram degrades to table-only (25 states, past the threshold)", () => {
        const manyStates = Array.from({ length: 25 }, (_unused, i) => state(`hash_${i}`, i % 2 === 0 ? "well" : "bimolecular", `state ${i}`))
        const manyChannels = Array.from({ length: 24 }, (_unused, i) => channel(`channel_${i}`, "association", `hash_${i}`, `hash_${i + 1}`))
        const { container } = renderDiagram(manyStates, manyChannels)
        expect(container.querySelector(".network-diagram-svg")).toBeNull()
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
        expect(screen.getAllByRole("row").length).toBeGreaterThan(25)
    })

    it("shows the explicit degrade banner text, never a silently blank diagram", () => {
        const manyStates = Array.from({ length: 25 }, (_unused, i) => state(`hash_${i}`, "well", `state ${i}`))
        renderDiagram(manyStates, [])
        expect(screen.getByText(/would not stay legible/)).toBeVisible()
    })

    it("under the threshold, no degrade banner is shown", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.queryByText(/would not stay legible/)).not.toBeInTheDocument()
    })
})

describe("NetworkDiagram -- legend counts are computed live, not hardcoded", () => {
    it("states the true well/bimolecular/kind counts for this fixture", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByText(/Well \(2 states here\)/)).toBeVisible()
        expect(screen.getByText(/Bimolecular \(2 states here/)).toBeVisible()
        expect(screen.getByText(/isomerization \(1 channel here\)/)).toBeVisible()
        expect(screen.getByText(/association \(2 channels here\)/)).toBeVisible()
        expect(screen.getByText(/no kinetics fit deposited \(1 of 3 channels here\)/)).toBeVisible()
    })

    it("a different fixture produces a different sentence, proving the count is not fixed", () => {
        const oneWell = [state("hash_only", "well", "only state")]
        renderDiagram(oneWell, [])
        expect(screen.getByText(/Well \(1 state here\)/)).toBeVisible()
        expect(screen.queryByText(/Well \(2 states here\)/)).not.toBeInTheDocument()
    })
})
