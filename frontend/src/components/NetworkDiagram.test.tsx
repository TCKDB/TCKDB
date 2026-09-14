import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
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

function energy(hash: string, energyKjMol: number): NetworkStateEnergy {
    return {
        state_composition_hash: hash,
        energy_kj_mol: energyKjMol,
        energy_zero_convention: "lowest_state",
        correction_convention: "electronic_only",
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

// source (hash_n2) = 180.1, sink (hash_n1) = 0.0, forward = 94.6 ->
// TS height = 180.1 + 94.6 = 274.7 (matches the brief's own verified
// figure for this exact channel). reverse chosen to agree: 274.7 - 0 = 274.7.
const HYDRAZINE_ENERGIES: NetworkStateEnergy[] = [
    energy("hash_n1", 0.0),
    energy("hash_n2", 180.1),
    energy("hash_n3", 380.9),
    // hash_n6: no deposited energy, deliberately.
]

const HYDRAZINE_BARRIERS: NetworkChannelBarrier[] = [
    barrier("channel_1", 94.6, 274.7),
]

function renderDiagram(
    states: NetworkState[],
    channels: NetworkChannel[],
    stateEnergies: NetworkStateEnergy[] | null = HYDRAZINE_ENERGIES,
    channelBarriers: NetworkChannelBarrier[] | null = HYDRAZINE_BARRIERS,
) {
    return render(
        <MemoryRouter>
            <NetworkDiagram states={states} channels={channels} stateEnergies={stateEnergies} channelBarriers={channelBarriers} />
        </MemoryRouter>,
    )
}

describe("NetworkDiagram -- invariant 1: a saddle point is drawn only for a channel with a deposited barrier", () => {
    it("draws exactly one saddle point (channel_1) though three channels exist", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        // Scoped to `.net-pes-saddle-link`, not `querySelector("svg")` --
        // the legend renders its own small illustrative <svg> icons first,
        // so "the first svg" would silently scope this to a 22x10 icon.
        const saddleLinks = container.querySelectorAll(".net-pes-saddle-link")
        expect(saddleLinks.length).toBeGreaterThan(0)
        expect(saddleLinks).toHaveLength(1)
    })

    it("draws no saddle point at all when NO channel carries a deposited barrier", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, HYDRAZINE_ENERGIES, [])
        expect(container.querySelectorAll(".net-pes-saddle-link")).toHaveLength(0)
    })

    it("states the true deposited-barrier count in prose, live from the data", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByText(/1 of 3 channels carries a deposited barrier/)).toBeVisible()
    })
})

describe("NetworkDiagram -- saddle height is source_energy + forward_barrier_kj_mol, rendered on the peak", () => {
    it("shows 274.7, never the bare forward barrier (94.6) or the reverse-derived figure alone", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const peakLabel = container.querySelector(".net-pes-peak-label")
        expect(peakLabel).not.toBeNull()
        expect(peakLabel!.textContent).toBe("274.7")
    })
})

describe("NetworkDiagram -- channel_key never reaches a reader, by any route", () => {
    it("appears in no user-facing text on the SVG: not a <text>, not an aria-label, not a title", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const labelled = Array.from(container.querySelectorAll("[aria-label], [title]"))
        expect(labelled.length).toBeGreaterThan(0)
        for (const el of labelled) {
            expect(el.getAttribute("aria-label") ?? "").not.toMatch(/channel_\d/)
            expect(el.getAttribute("title") ?? "").not.toMatch(/channel_\d/)
        }
        for (const el of Array.from(container.querySelectorAll("title, desc"))) {
            expect(el.textContent ?? "").not.toMatch(/channel_\d/)
        }
        for (const el of Array.from(container.querySelectorAll("svg text"))) {
            expect(el.textContent ?? "").not.toMatch(/channel_\d/)
        }
    })

    it("still describes the drawn saddle point by its chemistry, so the aria-label is not merely emptied", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const labels = Array.from(container.querySelectorAll("svg a.net-pes-saddle-link"))
            .map((el) => el.getAttribute("aria-label") ?? "")
        expect(labels.length).toBeGreaterThan(0)
        for (const label of labels) {
            expect(label).not.toBe("")
            expect(label).toMatch(/ to /)
        }
    })

    it("channel_1 appears as a data-channel-key attribute and in the table, never inside <text>", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const line = container.querySelector('polyline[data-channel-key="channel_1"]')
        expect(line).not.toBeNull()
        expect(Array.from(container.querySelectorAll('td[data-label="Channel"] code.data')).some((el) => el.textContent === "channel_1")).toBe(true)
    })
})

describe("NetworkDiagram -- every visible level label is composition.state_label", () => {
    it("renders each plotted state's state_label as SVG text, never the composition_hash", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const svgTexts = Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)
        expect(svgTexts).toContain("NN")
        expect(svgTexts).toContain("[NH-][NH3+]")
        expect(container.textContent ?? "").not.toContain("hash_n1")
    })
})

describe("NetworkDiagram -- a state with no deposited energy is omitted, never guessed", () => {
    it("does not draw a level for hash_n6, and states the omission count", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const levelLabels = Array.from(container.querySelectorAll(".net-pes-level-label")).map((el) => el.textContent)
        expect(levelLabels).not.toContain("2 [NH2]")
        expect(screen.getByText(/1 of 4 states has no deposited energy/)).toBeVisible()
    })
})

describe("NetworkDiagram -- the accessible tables always render, regardless of surface state", () => {
    it("renders both state and channel tables alongside the surface", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
        expect(screen.getAllByRole("row", { name: /./ }).length).toBeGreaterThan(HYDRAZINE_STATES.length)
    })

    it("still renders both tables when no state carries a deposited energy at all", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, [], [])
        expect(container.querySelector(".net-pes-svg")).toBeNull()
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
        expect(screen.getByText(/No state energies are deposited/)).toBeVisible()
    })
})

describe("NetworkDiagram -- legend counts are computed live, not hardcoded", () => {
    it("states the true well/bimolecular/kind counts for this fixture", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByText(/Well level \(2 of 3 plotted states\)/)).toBeVisible()
        expect(screen.getByText(/Bimolecular level \(1 of 3 plotted states\)/)).toBeVisible()
        expect(screen.getByText(/isomerization \(1 saddle point shown\)/)).toBeVisible()
    })

    it("a different fixture produces a different sentence, proving the count is not fixed", () => {
        const oneWell = [state("hash_only", "well", "only state")]
        const { container } = renderDiagram(oneWell, [], [energy("hash_only", 0)], [])
        expect(screen.getByText(/Well level \(1 of 1 plotted states\)/)).toBeVisible()
        expect(screen.queryByText(/Well level \(2 of/)).not.toBeInTheDocument()
        expect(container.querySelectorAll(".net-pes-level-link")).toHaveLength(1)
    })
})

describe("NetworkDiagram -- a saddle is drawn as a level bar (TS bar), not a point", () => {
    it("renders one .net-pes-ts-bar line per saddle, scoped to the PES svg (not a legend icon)", () => {
        // The legend renders its own small <svg> icons first (a
        // `container.querySelector("svg")` trap this file's own mutation
        // table has already hit once) -- scoped to `.net-pes-svg` so this
        // can never silently match a 22x10 swatch instead.
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const bars = container.querySelectorAll(".net-pes-svg .net-pes-ts-bar")
        const saddleLinks = container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")
        expect(saddleLinks.length).toBeGreaterThan(0)
        expect(bars).toHaveLength(saddleLinks.length)
    })

    it("the bar has two distinct x endpoints -- real width, not a collapsed point", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const bar = container.querySelector(".net-pes-svg .net-pes-ts-bar")!
        expect(bar.getAttribute("x1")).not.toBe(bar.getAttribute("x2"))
        expect(bar.getAttribute("y1")).toBe(bar.getAttribute("y2"))
    })

    it("carries channel_1 only as a data attribute on the bar, never as its text", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const bar = container.querySelector('.net-pes-svg .net-pes-ts-bar[data-channel-key="channel_1"]')
        expect(bar).not.toBeNull()
        expect(bar!.textContent ?? "").not.toMatch(/channel_\d/)
    })

    it("a connector leg's state-side endpoint is offset from that state's own centre, not launched from it", () => {
        // Regression coverage for a defect found only by screenshotting the
        // live hydrazine archive: a leg launched from a state's exact
        // centre (the same anchor its own caption sits on) cuts through
        // the caption's digits when that state has another connector
        // departing the same point. `NetworkDiagram.tsx` launches from
        // `saddle.sourceLegX`/`sinkLegX` instead. A HUB fixture (never a
        // 2-node pair, which this module deliberately leaves unoffset --
        // see `connectorLegX`'s own "no real outward side" comment) so the
        // peak is genuinely off to one side of the hub, not tied to it.
        const hubStates = [
            state("hub", "well", "hub"),
            state("left", "well", "left"),
            state("right", "well", "right"),
        ]
        const hubChannels = [
            channel("ch_left", "isomerization", "left", "hub"),
            channel("ch_right", "isomerization", "hub", "right"),
        ]
        const hubEnergies = [energy("hub", 0), energy("left", 50), energy("right", 100)]
        // Consistent forward/reverse pairs (source_energy + forward ==
        // sink_energy + reverse): ch_left 50+80==0+130; ch_right 0+160==100+60.
        const hubBarriers = [barrier("ch_left", 80, 130), barrier("ch_right", 160, 60)]
        const { container } = renderDiagram(hubStates, hubChannels, hubEnergies, hubBarriers)
        const hubLevel = container.querySelector('.net-pes-level-link[href="#state-row-hub"] .net-pes-level')!
        const hubCentreX = Number(hubLevel.getAttribute("x1")) + 34 // NETWORK_PES_LEVEL_HALF_WIDTH
        const hubY = hubLevel.getAttribute("y1")
        const polyline = container.querySelector('.net-pes-svg polyline[data-channel-key="ch_right"]')!
        const legPoint = polyline.getAttribute("points")!.split(" ")[0]
        expect(legPoint).not.toBe(`${hubCentreX},${hubY}`)
    })
})

describe("NetworkDiagram -- each level caption sits on its own opaque backing", () => {
    it("draws two .net-pes-caption-backing rects per plotted level (energy and label)", () => {
        // Regression coverage for a SEPARATE defect found the same way: a
        // y-axis gridline landing close to a caption's own y (by
        // coincidence of that state's energy) stayed visible through the
        // gaps BETWEEN glyphs even with the existing per-glyph stroke halo.
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const backings = container.querySelectorAll(".net-pes-svg .net-pes-caption-backing")
        const levels = container.querySelectorAll(".net-pes-svg .net-pes-level-link")
        expect(levels.length).toBeGreaterThan(0)
        expect(backings).toHaveLength(levels.length * 2)
    })

    it("sizes a backing rect to its OWN caption string, not a shared fixed width", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const rects = Array.from(container.querySelectorAll('.net-pes-level-link[href="#state-row-hash_n1"] .net-pes-caption-backing'))
        expect(rects).toHaveLength(2)
        const widths = rects.map((rect) => Number(rect.getAttribute("width")))
        // "NN" (label) is far shorter than "0.0 kJ/mol" (energy value) --
        // a shared/fixed-width backing would make these equal.
        expect(widths[0]).not.toBe(widths[1])
    })
})

describe("NetworkDiagram -- energy caption sits above a level's bar, species label below it", () => {
    it("for the NN level, the value (energy) text has a smaller SVG y than the label (species) text", () => {
        // Smaller y is HIGHER on an SVG canvas (y grows downward) -- the
        // reference figure draws the energy above the dash and the species
        // name below it, the reverse of this component's own layout before
        // this PR.
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const level = container.querySelector('.net-pes-svg .net-pes-level-link[href="#state-row-hash_n1"]')!
        const value = level.querySelector(".net-pes-level-value")!
        const label = level.querySelector(".net-pes-level-label")!
        expect(label.textContent).toBe("NN")
        expect(Number(value.getAttribute("y"))).toBeLessThan(Number(label.getAttribute("y")))
    })
})

describe("NetworkDiagram -- a state on no deposited barrier is drawn as its own group, with a divider and a stated count", () => {
    // hash_n3 (deposited energy 380.9) is the endpoint of channel_2, which
    // carries NO barrier in HYDRAZINE_BARRIERS -- so unlike hash_n6 (no
    // deposited energy at all, omitted from the surface entirely), hash_n3
    // IS plotted but reaches no other plotted state by any accepted
    // saddle, i.e. exactly the "unconnected" case.
    it("marks hash_n3's level data-unconnected and draws the dashed group divider", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const unconnectedLevel = container.querySelector('.net-pes-level-link[data-unconnected="true"]')
        expect(unconnectedLevel).not.toBeNull()
        expect(unconnectedLevel!.getAttribute("href")).toBe("#state-row-hash_n3")
        expect(container.querySelector('[data-testid="net-pes-group-divider"]')).not.toBeNull()
        expect(screen.getByText(/1 of 3 plotted states is not connected by any deposited barrier/)).toBeVisible()
    })

    it("draws no divider and states no such sentence when every plotted state is connected", () => {
        // A different fixture: two states joined by the ONE accepted
        // barrier -- nothing left unconnected.
        const states = [state("a", "well", "A"), state("b", "well", "B")]
        const channels = [channel("ch_ab", "isomerization", "a", "b")]
        const energies = [energy("a", 0), energy("b", 80)]
        const barriers = [barrier("ch_ab", 120, 40)]
        const { container } = renderDiagram(states, channels, energies, barriers)
        expect(container.querySelector('[data-testid="net-pes-group-divider"]')).toBeNull()
        expect(container.querySelector('[data-unconnected="true"]')).toBeNull()
        expect(screen.queryByText(/not connected by any deposited barrier/)).not.toBeInTheDocument()
    })
})

describe("NetworkDiagram -- a cyclic accepted-barrier graph states its own spanning-tree fallback", () => {
    it("shows the cycle-fallback sentence for a 3-state, 3-barrier triangle", () => {
        const states = [state("a", "well", "A"), state("b", "well", "B"), state("c", "well", "C")]
        const channels = [
            channel("ch_ab", "isomerization", "a", "b"),
            channel("ch_bc", "isomerization", "b", "c"),
            channel("ch_ca", "isomerization", "c", "a"),
        ]
        const energies = [energy("a", 0), energy("b", 50), energy("c", 100)]
        // Each pair internally consistent -- ch_ab: 0+80==50+30;
        // ch_bc: 50+60==100+10; ch_ca: 100+130==0+230.
        const barriers = [barrier("ch_ab", 80, 30), barrier("ch_bc", 60, 10), barrier("ch_ca", 130, 230)]
        renderDiagram(states, channels, energies, barriers)
        expect(screen.getByText(/deposited-barrier connectivity contains a cycle/)).toBeVisible()
    })

    it("says nothing about a cycle for the hydrazine tree, which has none", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.queryByText(/contains a cycle/)).not.toBeInTheDocument()
    })
})

// A fixture with TWO accepted barriers (unlike HYDRAZINE_*, which has only
// one) -- needed to prove hiding ONE channel leaves the OTHER's saddle,
// and every level, completely unaffected (invariant 2). Same hub shape as
// the "connector leg's state-side endpoint" fixture above, reused here for
// its own already-verified forward/reverse consistency.
const TWO_SADDLE_STATES: NetworkState[] = [
    state("hub", "well", "hub"),
    state("left", "well", "left"),
    state("right", "well", "right"),
]
const TWO_SADDLE_CHANNELS: NetworkChannel[] = [
    channel("ch_left", "isomerization", "left", "hub"),
    channel("ch_right", "isomerization", "hub", "right"),
]
const TWO_SADDLE_ENERGIES: NetworkStateEnergy[] = [energy("hub", 0), energy("left", 50), energy("right", 100)]
// ch_left: 50+80==0+130; ch_right: 0+160==100+60.
const TWO_SADDLE_BARRIERS: NetworkChannelBarrier[] = [barrier("ch_left", 80, 130), barrier("ch_right", 160, 60)]

function levelBarAttrs(container: HTMLElement, hash: string): { x1: string | null; x2: string | null; y1: string | null } {
    const bar = container.querySelector(`.net-pes-level-link[href="#state-row-${hash}"] .net-pes-level`)!
    return { x1: bar.getAttribute("x1"), x2: bar.getAttribute("x2"), y1: bar.getAttribute("y1") }
}

describe("NetworkDiagram -- per-channel show/hide (invariant 1: offers exactly the channels with an accepted barrier)", () => {
    it("renders one checkbox per ACCEPTED saddle, not one per channel on the network", () => {
        // HYDRAZINE_CHANNELS has 3 channels; only channel_1 carries a
        // deposited barrier (HYDRAZINE_BARRIERS).
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(container.querySelectorAll(".net-pes-channel-option")).toHaveLength(1)
        expect(container.querySelector('.net-pes-channel-option[data-channel-key="channel_1"]')).not.toBeNull()
    })

    it("renders no channel fieldset at all when no channel carries an accepted barrier", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, HYDRAZINE_ENERGIES, [])
        expect(container.querySelector(".net-pes-channel-fieldset")).toBeNull()
    })
})

describe("NetworkDiagram -- per-channel show/hide labels by chemistry, never by channel_key (invariant 1)", () => {
    it("labels each checkbox 'source to sink (kind)', with channel_key only in data-channel-key", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        const options = Array.from(container.querySelectorAll(".net-pes-channel-option"))
        const texts = options.map((el) => el.textContent)
        expect(texts).toContain("left to hub (isomerization)")
        expect(texts).toContain("hub to right (isomerization)")
        for (const text of texts) {
            expect(text).not.toMatch(/ch_left|ch_right/)
        }
        expect(container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"]')).not.toBeNull()
        expect(container.querySelector('.net-pes-channel-option[data-channel-key="ch_right"]')).not.toBeNull()
    })
})

describe("NetworkDiagram -- per-channel show/hide is a view filter only (invariant 2)", () => {
    it("hiding one channel removes only its own saddle, leaving every level position and the other saddle's height unchanged", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        const before = {
            hub: levelBarAttrs(container, "hub"),
            left: levelBarAttrs(container, "left"),
            right: levelBarAttrs(container, "right"),
            rightBar: container.querySelector('.net-pes-ts-bar[data-channel-key="ch_right"]')!,
        }
        const rightBarBefore = {
            x1: before.rightBar.getAttribute("x1"),
            x2: before.rightBar.getAttribute("x2"),
            y1: before.rightBar.getAttribute("y1"),
        }
        expect(container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")).toHaveLength(2)

        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"] input')!
        fireEvent.click(checkbox)

        // ch_left's own saddle is gone; ch_right's remains.
        expect(container.querySelector('.net-pes-svg .net-pes-ts-bar[data-channel-key="ch_left"]')).toBeNull()
        const saddleLinksAfter = container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")
        expect(saddleLinksAfter).toHaveLength(1)

        // Every level -- including "left", the very state ch_left touches
        // -- keeps its exact pre-hide x and y (invariant 2: never a relayout).
        expect(levelBarAttrs(container, "hub")).toEqual(before.hub)
        expect(levelBarAttrs(container, "left")).toEqual(before.left)
        expect(levelBarAttrs(container, "right")).toEqual(before.right)

        // ch_right's own saddle keeps its exact height (y1) and x position.
        const rightBarAfter = container.querySelector('.net-pes-ts-bar[data-channel-key="ch_right"]')!
        expect(rightBarAfter.getAttribute("x1")).toBe(rightBarBefore.x1)
        expect(rightBarAfter.getAttribute("x2")).toBe(rightBarBefore.x2)
        expect(rightBarAfter.getAttribute("y1")).toBe(rightBarBefore.y1)
    })
})

describe("NetworkDiagram -- the 'N of M shown' count always matches what is actually rendered", () => {
    it("legend and rendered saddle-link count agree, both before and after hiding a channel", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        expect(screen.getByText("Saddle points (2 of 2 shown)")).toBeVisible()
        expect(container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")).toHaveLength(2)

        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"] input')!
        fireEvent.click(checkbox)

        const legend = screen.getByText("Saddle points (1 of 2 shown)")
        expect(legend).toBeVisible()
        expect(container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")).toHaveLength(1)
    })
})

describe("NetworkDiagram -- hiding a channel never shrinks the accessible tables (invariant 3)", () => {
    it("the channel table still lists all 3 channels after the only drawable one is hidden", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="channel_1"] input')!
        fireEvent.click(checkbox)
        expect(container.querySelectorAll('.net-pes-svg .net-pes-saddle-link')).toHaveLength(0)
        const channelTable = screen.getByRole("table", { name: "Channels in this network" })
        expect(channelTable.querySelectorAll("tbody tr")).toHaveLength(3)
        expect(screen.getByRole("table", { name: "States in this network" }).querySelectorAll("tbody tr")).toHaveLength(4)
    })
})

describe("NetworkDiagram -- a filtered view says so, and how many of how many are shown (invariant 5)", () => {
    it("states nothing about filtering while every channel is visible", () => {
        renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        expect(screen.queryByText(/This view is filtered/)).not.toBeInTheDocument()
    })

    it("states the filtered count once a channel is hidden", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"] input')!
        fireEvent.click(checkbox)
        expect(screen.getByText(/This view is filtered: 1 of 2 drawable saddle points is shown/)).toBeVisible()
    })
})

describe("NetworkDiagram -- per-channel show/hide works in BOTH layout modes, not just 'energy'", () => {
    it("renders the same channel fieldset under the 'energy' layout radio", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        fireEvent.click(screen.getByLabelText(/By energy/))
        expect(container.querySelectorAll(".net-pes-channel-option")).toHaveLength(2)
        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"] input')!
        fireEvent.click(checkbox)
        expect(container.querySelector('.net-pes-svg .net-pes-ts-bar[data-channel-key="ch_left"]')).toBeNull()
        expect(container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")).toHaveLength(1)
    })

    it("a hidden channel stays hidden across a layout-mode switch, without re-clicking its checkbox", () => {
        const { container } = renderDiagram(TWO_SADDLE_STATES, TWO_SADDLE_CHANNELS, TWO_SADDLE_ENERGIES, TWO_SADDLE_BARRIERS)
        const checkbox = container.querySelector('.net-pes-channel-option[data-channel-key="ch_left"] input')!
        fireEvent.click(checkbox)
        expect(container.querySelector('.net-pes-svg .net-pes-ts-bar[data-channel-key="ch_left"]')).toBeNull()

        fireEvent.click(screen.getByLabelText(/By energy/))

        expect(container.querySelector('.net-pes-svg .net-pes-ts-bar[data-channel-key="ch_left"]')).toBeNull()
        expect(container.querySelectorAll(".net-pes-svg .net-pes-saddle-link")).toHaveLength(1)
        expect((checkbox as HTMLInputElement).checked).toBe(false)
    })
})

/**
 * MUTATION TABLE (`NetworkDiagram.test.tsx`)
 *
 * | # | Test | Mutation landed | Result |
 * |---|------|------------------|--------|
 * | 1 | "draws exactly one saddle point (channel_1) though three channels exist" | In `NetworkDiagram.tsx`, changed `layout.saddles.map(...)` to `channels.map((c) => ({ ...saddleFromChannelIgnoringBarrier }))`-shaped stand-in: concretely, replaced `layout.saddles` with `layout.saddles.concat(layout.saddles)` (doubling) as a cheap one-line stand-in for "draws a saddle for a channel with no barrier" | RED -- `saddleLinks` had length 2, `toHaveLength(1)` failed |
 * | 2 | "draws no saddle point at all when NO channel carries a deposited barrier" | Passed `HYDRAZINE_BARRIERS` instead of `[]` as the 4th arg in this one test (simulating the component ignoring an empty barrier list) | RED -- `toHaveLength(0)` failed, length was 1 |
 * | 3 | "shows 274.7, never the bare forward barrier" | In `NetworkDiagram.tsx`, changed `{saddle.heightKjMol.toFixed(1)}` to `{(94.6).toFixed(1)}` (hardcoding the bare forward barrier value) | RED -- expected "274.7", got "94.6" |
 * | 4 | "channel_1 appears as a data-channel-key attribute" | Removed `data-channel-key={saddle.channelKey ?? undefined}` from the `<polyline>` | RED -- `querySelector('polyline[data-channel-key="channel_1"]')` was null |
 * | 5 | "renders each plotted state's state_label as SVG text, never the composition_hash" | Changed `level.label` to `level.compositionHash` in the level `<text>` | RED -- `svgTexts` no longer contained "NN"/"[NH-][NH3+]"; contained "hash_n1" instead, failing the negative assertion |
 * | 6 | "does not draw a level for hash_n6" | Removed the `resolvedInServedOrder` filter in `networkPesLayout.ts` (placed every state regardless of a deposited energy) | RED -- `levelLabels` contained "2 [NH2]" |
 * | 7 | "still renders both tables when no state carries a deposited energy at all" | Wrapped `<NetworkStatesTable .../>`/`<NetworkChannelsTable .../>` in `{layout && (...)}` in `NetworkDiagram.tsx` (regressing invariant 5) | RED -- `screen.getByRole("table", ...)` threw, element not found |
 * | 8 | "renders one .net-pes-ts-bar line per saddle ..." / "the bar has two distinct x endpoints ..." / "carries channel_1 only as a data attribute on the bar ..." | Removed the `<line className="net-pes-ts-bar" ...>` element from the saddle's `<a>` | RED -- all 3 tests failed together (0 bars found; `bar` was `null`) |
 * | 9 | "for the NN level, the value (energy) text has a smaller SVG y than the label (species) text" | Swapped the two level `<text>` elements' `y` values back (`y-12`/`y+20` reversed) | RED -- `369.94` was not less than `337.94` |
 * | 10 | "marks hash_n3's level data-unconnected and draws the dashed group divider" | Changed `{dividerX != null && (...)}` to `{false && dividerX != null && (...)}` | RED -- `querySelector('[data-testid="net-pes-group-divider"]')` was null |
 * | 11 | "draws no divider ... when every plotted state is connected" | Changed the same condition to `{(dividerX != null \|\| true) && (...)}` (always render) | RED -- the divider `<line>` was found where none should exist |
 * | 12 | "shows the cycle-fallback sentence for a 3-state, 3-barrier triangle" | Changed `{layout.components.some(...) && (...)}` to `{false && layout.components.some(...) && (...)}` | RED -- `screen.getByText(/deposited-barrier connectivity contains a cycle/)` threw, element not found |
 * | 13 | "a connector leg's state-side endpoint is offset from that state's own centre, not launched from it" | Changed `saddle.sourceLegX` back to `saddle.sourceX` in the source-side `<polyline>` | RED -- the leg's first point equalled the hub's own bar centre, `not.toBe` failed |
 * | 14 | "draws two .net-pes-caption-backing rects per plotted level ..." / "sizes a backing rect to its OWN caption string ..." | Removed the label's own `<rect className="net-pes-caption-backing" ...>` element (kept the energy one) | RED -- both tests failed together (3 backings instead of 6; 1 rect instead of 2 for NN) |
 *
 * Each mutation was landed as a single edit, the named test confirmed red
 * (`npx vitest run src/components/NetworkDiagram.test.tsx`), then reverted
 * and confirmed via `git diff --stat` showing no changes and
 * `sha256sum -c` against a pre-mutation checksum of the touched file.
 */

/**
 * MUTATION TABLE (channel show/hide, `network-pes-channel-visibility`)
 *
 * | # | Test | Mutation landed | Result |
 * |---|------|------------------|--------|
 * | 1 | "hiding one channel removes only its own saddle, leaving every level position and the other saddle's height unchanged" | In `NetworkDiagram.tsx`, changed the level `<line>`'s `x1` to `level.x - NETWORK_PES_LEVEL_HALF_WIDTH + (hiddenChannelKeys.size > 0 ? 5 : 0)` -- a relayout keyed off "something is hidden" | RED -- `levelBarAttrs(container, "hub")` no longer equalled its pre-hide value (`x1` shifted `103` -> `108`) |
 * | 2 | "legend and rendered saddle-link count agree, both before and after hiding a channel" | In `NetworkPesChannelFieldset`, changed `shownCount` from the hidden-aware filter to a bare `saddles.length` | RED -- `screen.getByText("Saddle points (1 of 2 shown)")` was not found after hiding one channel (legend still said "2 of 2") |
 * | 3 | "renders one checkbox per ACCEPTED saddle, not one per channel on the network" | Changed `saddles={layout.saddles}` to `saddles={layout.saddles.concat(layout.saddles)}` at the `NetworkPesChannelFieldset` call site | RED -- `.net-pes-channel-option` count was 2, not 1 |
 * | 4 | "renders no channel fieldset at all when no channel carries an accepted barrier" | Changed `{layout && layout.saddles.length > 0 && (...)}` to `{layout && (...)}` | RED -- `.net-pes-channel-fieldset` was found (rendered with "0 of 0 shown") where none should exist |
 * | 5 | "labels each checkbox 'source to sink (kind)', with channel_key only in data-channel-key" | Changed the checkbox label text from `` `${saddle.sourceLabel} to ${saddle.sinkLabel} (${saddle.kind})` `` to `` `${saddle.channelKey}` `` | RED -- rendered texts were `["ch_left", "ch_right"]`, not the chemistry labels |
 * | 6 | "the channel table still lists all 3 channels after the only drawable one is hidden" | In `NetworkChannelsTable`, filtered its own `channels` prop to `channels.filter((c) => c.channel_key !== "channel_1")` before rendering rows -- simulating a table that shrinks along with the diagram | RED -- `channelTable.querySelectorAll("tbody tr")` had length 2, not 3 |
 * | 7 | "states nothing about filtering while every channel is visible" | Changed the filtered-note guard `{shownSaddleCount < layout.saddles.length && (...)}` to `{true && (...)}` | RED -- `screen.queryByText(/This view is filtered/)` found the note even with nothing hidden |
 * | 8 | "renders the same channel fieldset under the 'energy' layout radio" | Changed the fieldset's render guard to `{layout && layout.saddles.length > 0 && layoutMode === "connectivity" && (...)}` -- gating the control to one layout mode, the exact regression the brief warned against | RED -- `.net-pes-channel-option` count was 0 after switching to "By energy" |
 * | 9 | "a hidden channel stays hidden across a layout-mode switch, without re-clicking its checkbox" | In the "By energy" radio's `onChange`, added `setHiddenChannelKeys(new Set())` alongside `setLayoutMode("energy")` -- resetting the filter on every mode switch | RED -- `ch_left`'s `.net-pes-ts-bar` reappeared after switching to "By energy" even though it was hidden first |
 *
 * Each mutation was landed as a single edit, the named test confirmed red
 * (`npx vitest run src/components/NetworkDiagram.test.tsx -t "<test name>"`),
 * then reverted and confirmed via `sha256sum -c` against a pre-mutation
 * checksum of `NetworkDiagram.tsx` taken before mutation #1.
 */
