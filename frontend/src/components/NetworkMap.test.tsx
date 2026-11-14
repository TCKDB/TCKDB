import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState } from "../api/networkEntryApi"
import { NetworkMap } from "./NetworkMap"
import { NetworkChannelsTable, NetworkStatesTable } from "./NetworkTables"
import { channelHandle } from "../domain/publicText"
import { DEPOSITOR_SENTINEL, expectNoDepositorText } from "../test/depositorSentinel"

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

function barrier(channelKey: string, forward: number, reverse: number): NetworkChannelBarrier {
    return {
        channel_key: channelKey,
        reaction_entry_ref: `rxe_${channelKey}`,
        transition_state_entry_ref: `tse_${channelKey}`,
        forward_barrier_kj_mol: forward,
        reverse_barrier_kj_mol: reverse,
        energy_zero_convention: "lowest_state",
        correction_convention: "electronic_only",
    }
}

// A network shaped like the live hydrazine archive: 4 states, 3 channels,
// but only ONE channel (channel_1) carries a deposited barrier -- the
// other two (channel_2, channel_5) must produce no saddle point at all
// (invariant 1). hash_n6 deliberately carries no deposited energy, so it
// must be OMITTED from the surface (never guessed), counted in the
// "no deposited energy" note.
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

const HYDRAZINE_BARRIERS: NetworkChannelBarrier[] = [
    barrier("channel_1", 94.6, 274.7),
]


function renderMap(
    states: NetworkState[] = HYDRAZINE_STATES,
    channels: NetworkChannel[] = HYDRAZINE_CHANNELS,
    barriers: NetworkChannelBarrier[] | null = HYDRAZINE_BARRIERS,
    extra: { compact?: boolean; highlightReactionEntryRef?: string } = {},
) {
    return render(
        <MemoryRouter>
            <NetworkMap states={states} channels={channels} channelBarriers={barriers} {...extra} />
            <NetworkStatesTable states={states} />
            <NetworkChannelsTable channels={channels} states={states} />
        </MemoryRouter>,
    )
}

describe("NetworkMap: a triangle only for a channel with a deposited barrier", () => {
    it("draws one triangle although three channels exist", () => {
        const { container } = renderMap()
        expect(container.querySelectorAll("svg.net-map-svg .net-map-ts")).toHaveLength(1)
        expect(container.querySelectorAll("svg a.net-map-link path.net-map-edge")).toHaveLength(3)
    })

    it("draws no triangle when no barrier is deposited, and when the barrier list is absent", () => {
        expect(renderMap(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, []).container.querySelectorAll("svg.net-map-svg .net-map-ts")).toHaveLength(0)
        cleanup()
        expect(renderMap(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, null).container.querySelectorAll("svg.net-map-svg .net-map-ts")).toHaveLength(0)
    })

    it("draws no triangle for a barrier whose channel is not on the network", () => {
        const { container } = renderMap(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, [barrier("channel_9", 10, 20)])
        expect(container.querySelectorAll("svg.net-map-svg .net-map-ts")).toHaveLength(0)
    })
})

describe("NetworkMap: shapes", () => {
    it("draws a circle for a stable molecule and a hexagon for molecules apart", () => {
        const { container } = renderMap()
        const svg = container.querySelector("svg.net-map-svg")!
        expect(svg.querySelectorAll("circle.net-map-node")).toHaveLength(2)
        const hexagons = svg.querySelectorAll("polygon.net-map-node--end")
        expect(hexagons).toHaveLength(2)
        expect(hexagons[0].getAttribute("points")!.split(" ")).toHaveLength(6)
    })

    it("marks a channel with no rate as dashed", () => {
        const { container } = renderMap()
        expect(container.querySelectorAll("svg path.net-map-edge--dashed")).toHaveLength(1)
    })
})

describe("NetworkMap: accessible names and structure", () => {
    it("is a group with a caption, not an image", () => {
        const { container } = renderMap()
        const svg = container.querySelector("svg.net-map-svg")!
        expect(svg.getAttribute("role")).toBe("group")
        const caption = document.getElementById(svg.getAttribute("aria-labelledby")!)!
        expect(caption.textContent).toBe("4 states joined by 3 channels. Position carries no meaning.")
    })

    it("names each link by its chemistry, in plain words", () => {
        renderMap()
        expect(screen.getByRole("link", { name: "State NN, a stable molecule" })).toHaveAttribute("href", "#state-row-hash_n1")
        expect(screen.getByRole("link", { name: "State 2 [NH2], two molecules apart" })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: "Channel 1, isomerization, [NH-][NH3+] to NN, transition state with a deposited barrier" })).toHaveAttribute("href", "#channel-row-0")
    })

    it("draws every state, including one with no energy", () => {
        const { container } = renderMap()
        expect(Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)).toContain("2 [NH2]")
    })

    it("has no links and no captions when compact, and highlights this reaction's channels", () => {
        const channels = HYDRAZINE_CHANNELS.map((c) => (c.channel_key === "channel_2" ? { ...c, microreactions: [{ reaction_entry_ref: "rxe_here", path_kind: "saddle_point" }] } : c))
        const { container } = renderMap(HYDRAZINE_STATES, channels, null, { compact: true, highlightReactionEntryRef: "rxe_here" })
        const svg = container.querySelector("svg.net-map-svg")!
        expect(svg.querySelectorAll("a")).toHaveLength(0)
        expect(svg.querySelectorAll("text")).toHaveLength(0)
        expect(svg.querySelectorAll("path.net-map-edge--here")).toHaveLength(1)
    })
})

describe("NetworkMap: the tables carry everything", () => {
    it("renders both tables with every state and channel", () => {
        renderMap()
        expect(within(screen.getByRole("table", { name: "States in this network" })).getAllByRole("row")).toHaveLength(HYDRAZINE_STATES.length + 1)
        expect(within(screen.getByRole("table", { name: "Channels in this network" })).getAllByRole("row")).toHaveLength(HYDRAZINE_CHANNELS.length + 1)
    })

    it("prints a channel's position, never its key, and keeps the key only as an opaque handle", () => {
        const { container } = renderMap()
        expect(container.textContent).not.toContain("channel_1")
        expect(container.querySelector(`path[data-channel-key="${channelHandle("channel_1")}"]`)).not.toBeNull()
    })

    it("lists no barrier or position as an energy", () => {
        const { container } = renderMap()
        expect(container.querySelector("svg")!.textContent).not.toMatch(/kJ/)
    })
})

describe("NetworkMap: legend", () => {
    it("states live counts in plain words", () => {
        renderMap()
        const legend = screen.getByRole("list", { name: "What the shapes mean" })
        expect(legend.textContent).toContain("2 stable molecules (circle)")
        expect(legend.textContent).toContain("2 pairs of molecules apart (hexagon)")
        expect(legend.textContent).toContain("3 channels (line); dashed when no rate is deposited (1)")
        expect(legend.textContent).toContain("A transition state with a deposited barrier (1)")
    })

    it("changes with the data", () => {
        renderMap(HYDRAZINE_STATES.slice(0, 1), [], null)
        expect(screen.getByRole("list", { name: "What the shapes mean" }).textContent).toContain("1 stable molecule (circle)")
    })
})

describe("NetworkMap: no depositor-typed text", () => {
    it("keeps a typed channel key and state label out of every text, id and attribute", () => {
        const typed = (value: unknown) => JSON.parse(
            JSON.stringify(value)
                .replaceAll('"channel_1"', `"${DEPOSITOR_SENTINEL}_channel_key_a"`)
                .replaceAll('"channel_2"', `"${DEPOSITOR_SENTINEL}_channel_key_b"`)
                .replaceAll('"channel_5"', `"${DEPOSITOR_SENTINEL}_channel_key_c"`),
        )
        const states = HYDRAZINE_STATES.map((state, index) => ({ ...state, label: `${DEPOSITOR_SENTINEL}_state_label_${index}` }))
        const channels = typed(HYDRAZINE_CHANNELS) as NetworkChannel[]
        const barriers = typed(HYDRAZINE_BARRIERS) as NetworkChannelBarrier[]
        const { container } = renderMap(states, channels, barriers)
        expect(container.querySelectorAll("svg.net-map-svg .net-map-ts").length).toBeGreaterThan(0)
        expect(container.querySelectorAll('td[data-label="Channel"]').length).toBe(HYDRAZINE_CHANNELS.length)
        expectNoDepositorText()
    })
})
